"""CPU usage is relative to available container capacity; source errors stay visible."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'monitor_collectors', Path(__file__).parents[1] / 'apps/dev-monitor/backend/collectors.py')
collectors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collectors)


class CollectorsTests(unittest.TestCase):
    def setUp(self):
        collectors._previous_cpu = None

    def capacity(self, cpuset, quota, affinity=range(32)):
        files = {'cpuset.cpus.effective': cpuset, 'cpu.max': quota}
        with patch.object(collectors.os, 'cpu_count', return_value=32), \
             patch.object(collectors.os, 'sched_getaffinity', return_value=set(affinity)), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'read_text', lambda path: files[path.name]):
            return collectors.cpu_capacity()

    def test_host_cpu_count_does_not_hide_cpuset_or_fractional_quota(self):
        self.assertEqual(2, self.capacity('3,8', 'max 100000'))
        self.assertEqual(1.5, self.capacity('0-15', '150000 100000'))
        self.assertEqual(3, self.capacity('0-7,12-15', 'max 100000', range(3)))

    def test_two_busy_cores_in_two_core_container_is_full_usage(self):
        with patch.object(collectors, 'cpu_capacity', return_value=2), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'read_text', side_effect=['usage_usec 1000000', 'usage_usec 11000000']), \
             patch.object(collectors.time, 'monotonic', side_effect=[10, 15]), \
             patch.object(collectors.os, 'getloadavg', return_value=(0, 0, 0)):
            self.assertIsNone(collectors.cpu_info()['percent'])
            self.assertEqual(100, collectors.cpu_info()['percent'])

    def test_cpu_counter_reset_waits_for_next_sample(self):
        collectors._previous_cpu = ('cgroup', 10, 5000000)
        with patch.object(collectors, 'cpu_capacity', return_value=2), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(Path, 'read_text', return_value='usage_usec 1000000'), \
             patch.object(collectors.time, 'monotonic', return_value=15):
            self.assertIsNone(collectors.cpu_info()['percent'])

    def test_source_error_does_not_erase_other_observations(self):
        with patch.object(collectors, 'host_info', return_value={'hostname': 'fixture'}), \
             patch.object(collectors, 'cpu_info', return_value={'percent': 50}), \
             patch.object(collectors, 'memory_info', side_effect=OSError('source unavailable')), \
             patch.object(collectors, 'disk_info', return_value={}), \
             patch.object(collectors, 'network_info', return_value={}), \
             patch.object(collectors, 'services_info', return_value=[]):
            result = collectors.collect(Path('/fixture'))
        self.assertIsNone(result['memory'])
        self.assertEqual({'memory': 'source unavailable'}, result['errors'])
        self.assertEqual('fixture', result['host']['hostname'])
        self.assertEqual(50, result['cpu']['percent'])
