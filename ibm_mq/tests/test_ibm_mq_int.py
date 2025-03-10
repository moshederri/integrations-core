# (C) Datadog, Inc. 2018-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import datetime as dt
import logging
import os

import mock
import pytest
from six import iteritems

from datadog_checks.base import AgentCheck
from datadog_checks.base.utils.time import ensure_aware_datetime
from datadog_checks.dev.utils import get_metadata_metrics
from datadog_checks.ibm_mq import IbmMqCheck
from datadog_checks.ibm_mq.collectors import ChannelMetricCollector, QueueMetricCollector

from . import common
from .common import QUEUE_METRICS, assert_all_metrics, skip_windows_ci

pytestmark = [skip_windows_ci, pytest.mark.usefixtures("dd_environment"), pytest.mark.integration]


def test_no_msg_errors_are_caught(get_check, instance, caplog, dd_run_check):
    # Late import to ignore missing library for e2e
    from pymqi import MQMIError, PCFExecute
    from pymqi.CMQC import MQCC_FAILED, MQRC_NO_MSG_AVAILABLE

    caplog.set_level(logging.WARNING)
    m = mock.MagicMock()
    with mock.patch('datadog_checks.ibm_mq.collectors.channel_metric_collector.pymqi.PCFExecute', new=m), mock.patch(
        'datadog_checks.ibm_mq.collectors.queue_metric_collector.pymqi.PCFExecute', new=m
    ), mock.patch('datadog_checks.ibm_mq.collectors.stats_collector.pymqi.PCFExecute', new=m):
        error = MQMIError(MQCC_FAILED, MQRC_NO_MSG_AVAILABLE)
        m.side_effect = error
        m.unpack = PCFExecute.unpack
        check = get_check(instance)
        dd_run_check(check)

        assert not caplog.records


def test_unknown_service_check(aggregator, get_check, instance, caplog, dd_run_check):
    # Late import to ignore missing library for e2e
    from pymqi import MQMIError, PCFExecute
    from pymqi.CMQC import MQCC_FAILED, MQRC_NO_MSG_AVAILABLE

    m = mock.MagicMock()
    with mock.patch('datadog_checks.ibm_mq.collectors.channel_metric_collector.pymqi.PCFExecute', new=m), mock.patch(
        'datadog_checks.ibm_mq.collectors.queue_metric_collector.pymqi.PCFExecute', new=m
    ), mock.patch('datadog_checks.ibm_mq.collectors.stats_collector.pymqi.PCFExecute', new=m):
        error = MQMIError(MQCC_FAILED, MQRC_NO_MSG_AVAILABLE)
        m.side_effect = error
        m.unpack = PCFExecute.unpack
        check = get_check(instance)
        dd_run_check(check)

        tags = [
            'queue_manager:{}'.format(common.QUEUE_MANAGER),
            'mq_host:{}'.format(common.HOST),
            'port:{}'.format(common.PORT),
            'connection_name:{}({})'.format(common.HOST, common.PORT),
            'foo:bar',
        ]

        # assert all channel serive checks are UNKNOWN
        channels = [common.CHANNEL, common.BAD_CHANNEL, '*']
        for channel in channels:
            channel_tags = tags + ['channel:{}'.format(channel)]
            aggregator.assert_service_check('ibm_mq.channel', check.UNKNOWN, tags=channel_tags, count=1)


def test_check_cant_connect(aggregator, get_check, instance, dd_run_check):
    instance['queue_manager'] = "not_real"

    with pytest.raises(Exception, match=r'MQI Error'):
        check = get_check(instance)
        dd_run_check(check)

        tags = [
            'connection_name:{}({})'.format(common.HOST, common.PORT),
            'foo:bar',
            'port:{}'.format(common.PORT),
            'queue_manager:not_real',
            'channel:{}'.format(common.CHANNEL),
        ]
        hostname = common.HOST

        aggregator.assert_service_check(IbmMqCheck.SERVICE_CHECK, check.CRITICAL, tags=tags, count=1, hostname=hostname)
        aggregator.assert_service_check(
            ChannelMetricCollector.CHANNEL_SERVICE_CHECK, check.CRITICAL, tags=tags, count=1, hostname=hostname
        )


def test_errors_are_logged(get_check, instance, caplog, dd_run_check):
    # Late import to ignore missing library for e2e
    from pymqi import MQMIError, PCFExecute
    from pymqi.CMQC import MQCC_FAILED, MQRC_BUFFER_ERROR

    caplog.set_level(logging.WARNING)
    m = mock.MagicMock()
    with mock.patch('datadog_checks.ibm_mq.collectors.channel_metric_collector.pymqi.PCFExecute', new=m), mock.patch(
        'datadog_checks.ibm_mq.collectors.queue_metric_collector.pymqi.PCFExecute', new=m
    ), mock.patch('datadog_checks.ibm_mq.collectors.stats_collector.pymqi.PCFExecute', new=m):
        error = MQMIError(MQCC_FAILED, MQRC_BUFFER_ERROR)
        m.side_effect = error
        m.unpack = PCFExecute.unpack
        check = get_check(instance)
        dd_run_check(check)

        assert caplog.records


@pytest.mark.parametrize(
    'override_hostname',
    [False, True],
)
def test_check_metrics_and_service_checks(aggregator, get_check, instance, seed_data, override_hostname, dd_run_check):
    instance['mqcd_version'] = os.getenv('IBM_MQ_VERSION')
    instance['override_hostname'] = override_hostname
    check = get_check(instance)
    dd_run_check(check)

    tags = [
        'connection_name:{}({})'.format(common.HOST, common.PORT),
        'foo:bar',
        'port:{}'.format(common.PORT),
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
    ]
    if override_hostname:
        hostname = common.HOST
    else:
        tags.append('mq_host:{}'.format(common.HOST))
        hostname = None

    assert_all_metrics(aggregator, minimum_tags=tags, hostname=hostname)

    channel_tags = tags + ['channel:{}'.format(common.CHANNEL)]
    aggregator.assert_service_check(
        ChannelMetricCollector.CHANNEL_SERVICE_CHECK, check.OK, tags=channel_tags, count=1, hostname=hostname
    )
    aggregator.assert_service_check(
        QueueMetricCollector.QUEUE_MANAGER_SERVICE_CHECK, check.OK, channel_tags, hostname=hostname
    )

    queue_tags = channel_tags + ['queue:DEV.QUEUE.1']
    aggregator.assert_service_check(QueueMetricCollector.QUEUE_SERVICE_CHECK, check.OK, queue_tags, hostname=hostname)

    bad_channel_tags = tags + ['channel:{}'.format(common.BAD_CHANNEL)]
    aggregator.assert_service_check('ibm_mq.channel', check.CRITICAL, tags=bad_channel_tags, count=1, hostname=hostname)

    discoverable_tags = tags + ['channel:*']
    aggregator.assert_service_check('ibm_mq.channel', check.OK, tags=discoverable_tags, count=1, hostname=hostname)


def test_check_connection_name_one(aggregator, get_check, instance_with_connection_name, dd_run_check):
    instance_with_connection_name['mqcd_version'] = os.getenv('IBM_MQ_VERSION')

    check = get_check(instance_with_connection_name)
    dd_run_check(check)

    assert_all_metrics(aggregator)

    tags = [
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
        'connection_name:{}({})'.format(common.HOST, common.PORT),
    ]

    channel_tags = tags + ['channel:{}'.format(common.CHANNEL)]
    aggregator.assert_service_check('ibm_mq.channel', check.OK, tags=channel_tags, count=1)


def test_check_connection_names_multi(aggregator, get_check, instance_with_connection_name, dd_run_check):
    instance = instance_with_connection_name
    instance['mqcd_version'] = os.getenv('IBM_MQ_VERSION')
    instance['connection_name'] = "localhost(9999),{}".format(instance['connection_name'])

    check = get_check(instance)
    dd_run_check(check)

    assert_all_metrics(aggregator)


def test_check_all(aggregator, get_check, instance_collect_all, seed_data, dd_run_check):
    check = get_check(instance_collect_all)
    dd_run_check(check)

    assert_all_metrics(aggregator)


def test_check_skip_reset_queue_metrics(aggregator, get_check, instance_collect_all, seed_data, dd_run_check):
    instance_collect_all['collect_reset_queue_metrics'] = False
    check = get_check(instance_collect_all)
    dd_run_check(check)

    aggregator.assert_metric('ibm_mq.queue.high_q_depth', count=0)
    aggregator.assert_metric('ibm_mq.queue.msg_deq_count', count=0)
    aggregator.assert_metric('ibm_mq.queue.msg_enq_count', count=0)
    aggregator.assert_metric('ibm_mq.queue.time_since_reset', count=0)


@pytest.mark.parametrize(
    'channel_status_mapping, expected_service_check_status',
    [({'running': 'warning'}, AgentCheck.WARNING), ({'running': 'critical'}, AgentCheck.CRITICAL)],
)
def test_integration_custom_mapping(
    aggregator, get_check, instance, channel_status_mapping, expected_service_check_status, dd_run_check
):
    instance['channel_status_mapping'] = channel_status_mapping
    check = get_check(instance)

    dd_run_check(check)

    aggregator.assert_service_check('ibm_mq.channel.status', expected_service_check_status)


def test_check_queue_pattern(aggregator, get_check, instance_queue_pattern, seed_data, dd_run_check):
    check = get_check(instance_queue_pattern)
    dd_run_check(check)

    assert_all_metrics(aggregator)


def test_check_queue_regex(aggregator, get_check, instance_queue_regex, seed_data, dd_run_check):
    check = get_check(instance_queue_regex)
    dd_run_check(check)

    assert_all_metrics(aggregator)


def test_check_channel_count(aggregator, get_check, instance_queue_regex_tag, seed_data):
    # Late import to not require it for e2e
    import pymqi

    metrics_to_assert = {
        "inactive": 0,
        "binding": 0,
        "starting": 0,
        "running": 1,
        "stopping": 0,
        "retrying": 0,
        "stopped": 0,
        "requesting": 0,
        "paused": 0,
        "initializing": 0,
        "unknown": 0,
    }

    check = get_check(instance_queue_regex_tag)
    check.channel_metric_collector._submit_channel_count(
        'my_channel', pymqi.CMQCFC.MQCHS_RUNNING, ["channel:my_channel"]
    )

    for status, expected_value in iteritems(metrics_to_assert):
        aggregator.assert_metric(
            'ibm_mq.channel.count', expected_value, tags=["channel:my_channel", "status:" + status]
        )
    aggregator.assert_metric('ibm_mq.channel.count', 0, tags=["channel:my_channel", "status:unknown"])


def test_check_channel_count_status_unknown(aggregator, get_check, instance_queue_regex_tag, seed_data):
    metrics_to_assert = {
        "inactive": 0,
        "binding": 0,
        "starting": 0,
        "running": 0,
        "stopping": 0,
        "retrying": 0,
        "stopped": 0,
        "requesting": 0,
        "paused": 0,
        "initializing": 0,
        "unknown": 1,
    }

    check = get_check(instance_queue_regex_tag)
    check.channel_metric_collector._submit_channel_count('my_channel', 123, ["channel:my_channel"])

    for status, expected_value in iteritems(metrics_to_assert):
        aggregator.assert_metric(
            'ibm_mq.channel.count', expected_value, tags=["channel:my_channel", "status:" + status]
        )


def test_check_regex_tag(aggregator, get_check, instance_queue_regex_tag, seed_data, dd_run_check):
    check = get_check(instance_queue_regex_tag)
    dd_run_check(check)

    tags = [
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
        'mq_host:{}'.format(common.HOST),
        'port:{}'.format(common.PORT),
        'connection_name:{}({})'.format(common.HOST, common.PORT),
        'channel:{}'.format(common.CHANNEL),
        'queue:{}'.format(common.QUEUE),
        'foo:bar',
    ]

    for metric, _ in QUEUE_METRICS:
        aggregator.assert_metric(metric, tags=tags)


def test_stats_metrics(aggregator, get_check, instance, dd_run_check):
    # Late import to ignore missing library for e2e
    from pymqi import MQMIError
    from pymqi.CMQC import MQCC_FAILED, MQRC_NO_MSG_AVAILABLE

    instance['mqcd_version'] = os.getenv('IBM_MQ_VERSION')

    check = get_check(instance)

    # make sure time is before fixture messages start time
    check._config.instance_creation_datetime = ensure_aware_datetime(dt.datetime(year=2000, month=1, day=1))

    with open(os.path.join(common.HERE, 'fixtures', 'statistics_channel.data'), 'rb') as channel_file, open(
        os.path.join(common.HERE, 'fixtures', 'statistics_queue.data'), 'rb'
    ) as queue_file:
        channel_data = channel_file.read()
        queue_data = queue_file.read()
        with mock.patch('datadog_checks.ibm_mq.collectors.stats_collector.Queue') as queue:
            queue().get.side_effect = [
                channel_data,
                queue_data,
                MQMIError(MQCC_FAILED, MQRC_NO_MSG_AVAILABLE),
            ]
            dd_run_check(check)

    common_tags = [
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
        'mq_host:{}'.format(common.HOST),
        'port:{}'.format(common.PORT),
        'connection_name:{}({})'.format(common.HOST, common.PORT),
        'foo:bar',
    ]
    channel_tags = common_tags + [
        'channel:GCP.A',
        'channel_type:clusrcvr',
        'remote_q_mgr_name:QM2',
        'connection_name:192.168.32.2',
    ]
    for metric, metric_type in common.CHANNEL_STATS_METRICS:
        aggregator.assert_metric(metric, metric_type=getattr(aggregator, metric_type.upper()), tags=channel_tags)

    queue_tags = common_tags + [
        'definition_type:predefined',
        'queue:SYSTEM.CHLAUTH.DATA.QUEUE',
        'queue_type:local',
    ]
    for metric, metric_type in common.QUEUE_STATS_METRICS:
        aggregator.assert_metric(metric, metric_type=getattr(aggregator, metric_type.upper()), tags=queue_tags)

    queue_tags_persistent = queue_tags + ['persistent:true']

    for metric, metric_type in common.QUEUE_STATS_LIST_METRICS:
        aggregator.assert_metric(
            metric, metric_type=getattr(aggregator, metric_type.upper()), tags=queue_tags_persistent
        )

    queue_tags_nonpersistent = queue_tags + ['persistent:false']

    for metric, metric_type in common.QUEUE_STATS_LIST_METRICS:
        aggregator.assert_metric(
            metric, metric_type=getattr(aggregator, metric_type.upper()), tags=queue_tags_nonpersistent
        )

    assert_all_metrics(aggregator)
    aggregator.assert_metrics_using_metadata(get_metadata_metrics())
