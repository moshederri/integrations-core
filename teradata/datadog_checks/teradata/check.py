# (C) Datadog, Inc. 2022-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import json
import time
from contextlib import closing, contextmanager

try:
    import teradatasql

    TERADATASQL_IMPORT_ERROR = None
except ImportError as e:
    teradatasql = None
    TERADATASQL_IMPORT_ERROR = e


from datadog_checks.base import AgentCheck
from datadog_checks.base.constants import ServiceCheck
from datadog_checks.base.utils.db import QueryManager

from .config import TeradataConfig
from .queries import COLLECT_RES_USAGE, DEFAULT_QUERIES

SERVICE_CHECK_CONNECT = 'can_connect'
SERVICE_CHECK_QUERY = 'can_query'


class TeradataCheck(AgentCheck):
    __NAMESPACE__ = 'teradata'

    def __init__(self, name, init_config, instances):
        super(TeradataCheck, self).__init__(name, init_config, instances)
        self.config = TeradataConfig(self.instance)
        self._connection = None
        self._server_tag = 'teradata_server:{}'.format(self.config.server)
        self._port_tag = 'teradata_port:{}'.format(self.config.port)
        self._tags = [self._server_tag, self._port_tag] + self.config.tags

        manager_queries = []
        if self.config.collect_res_usage:
            manager_queries.extend(COLLECT_RES_USAGE)
        else:
            manager_queries.extend(DEFAULT_QUERIES)

        self._query_manager = QueryManager(
            self,
            self._execute_query_raw,
            queries=manager_queries,
            tags=self._tags,
            error_handler=self._executor_error_handler,
        )
        self.check_initializations.append(self._query_manager.compile_queries)

        self._connection_errors = 0
        self._query_errors = 0

    def check(self, _):
        self._connection_errors = 0
        self._query_errors = 0

        with self.connect() as conn:
            if conn:
                self._connection = conn
                self._query_manager.execute()

        if self._connection_errors:
            self.service_check(SERVICE_CHECK_CONNECT, ServiceCheck.CRITICAL, tags=self._tags)
        else:
            self.service_check(SERVICE_CHECK_CONNECT, ServiceCheck.OK, tags=self._tags)

        if self._query_errors:
            self.service_check(SERVICE_CHECK_QUERY, ServiceCheck.CRITICAL, tags=self._tags)
        else:
            self.service_check(SERVICE_CHECK_QUERY, ServiceCheck.OK, tags=self._tags)

    def _execute_query_raw(self, query):
        with closing(self._connection.cursor()) as cursor:
            query = query.format(self.config.database)
            cursor.execute(query)
            if cursor.rowcount < 1:
                self.log.warning('Failed to fetch records from query: `%s`.', query)
                self._query_errors += 1
                return None
            else:
                for row in cursor.fetchall():
                    try:
                        yield self._validate_timestamp(row, query)
                    except Exception:
                        self.log.debug('Unable to validate Resource Usage View timestamps, skipping row.')
                        yield row

    def _executor_error_handler(self, error):
        self._query_errors += 1
        if self._connection:
            try:
                self._connection.close()
            except Exception as e:
                self.log.warning("Couldn't close the connection after a query failure: %s", str(e))
        self._connection = None
        return error

    @contextmanager
    def connect(self):
        conn = None
        if TERADATASQL_IMPORT_ERROR:
            self._connection_errors += 1
            self.log.error(
                'Teradata SQL Driver module is unavailable. Please double check your installation and refer to the '
                'Datadog documentation for more information. %s',
                TERADATASQL_IMPORT_ERROR,
            )
            raise TERADATASQL_IMPORT_ERROR
        else:
            self.log.debug('Connecting to Teradata...')
            conn_params = self._build_connect_params()

            try:
                conn = teradatasql.connect(json.dumps(conn_params))
                self.log.debug('Connected to Teradata.')
                yield conn
            except Exception as e:
                self.service_check(SERVICE_CHECK_CONNECT, ServiceCheck.CRITICAL, tags=self._tags)
                self.log.error('Unable to connect to Teradata. %s.', e)
                raise
            finally:
                if conn:
                    conn.close()

    def _build_connect_params(self):
        return {
            'host': self.config.server,
            'account': self.config.account,
            'database': self.config.database,
            'dbs_port': str(self.config.port),
            'logmech': self.config.auth_mechanism,
            'logdata': self.config.auth_data,
            'user': self.config.username,
            'password': self.config.password,
            'https_port': str(self.config.https_port),
            'sslmode': self.config.ssl_mode,
            'sslprotocol': self.config.ssl_protocol,
        }

    def _validate_timestamp(self, row, query):
        if 'DBC.ResSpmaView' in query:
            now = time.time()
            row_ts = row[0]
            if type(row_ts) is not int:
                msg = 'Timestamp `{}` is invalid. Skipping row.'.format(row_ts)
                self.log.warning(msg)
                self._query_errors += 1
                raise Exception(msg)
            else:
                diff = now - row_ts
                # Valid metrics should be no more than 10 min in the future or 1h in the past
                if (diff > 3600) or (diff < -600):
                    msg = 'Resource Usage stats are invalid. {}'
                    if diff > 3600:
                        msg = msg.format(
                            'Row timestamp is more than 1h in the past. Is `SPMA` Resource Usage Logging enabled?'
                        )
                    elif diff < -600:
                        msg = msg.format(
                            'Row timestamp is more than 10 min in the future. Try checking system time settings.'
                        )
                    self.log.warning(msg)
                    self._query_errors += 1
                    raise Exception(msg)
        return row
