from prometheus_client import CollectorRegistry, Gauge, write_to_textfile
from twisted.internet import reactor
import os

# Define prometheus metrics and it's explanation
METRIC_INFO = {
    'transactions': 'Number of transactions',
    'blocks': 'Number of blocks',
    'hash_rate': 'Hash rate of the network',
    'peers': 'Peers connected in the network'
}


class PrometheusMetricsExporter:
    """ Class that sends hathor metrics to a node exporter that will be read by Prometheus
    """

    def __init__(self, metrics, path):
        """
        :param metrics: Metric object that stores all the hathor metrics
        :type metrics: :py:class:`hathor.metrics.Metrics`

        :param path: Path to save the prometheus file
        :type path: str
        """
        self.metrics = metrics

        # Full filepath with filename
        self.filepath = os.path.join(path, 'hathor.prom')

        # Stores all Gauge objects for each metric (key is the metric name)
        # Dict[str, prometheus_client.Gauge]
        self.metric_gauges = {}

        # Setup initial prometheus lib objects for each metric
        self._initial_setup()

        # Interval in which the write data method will be called
        self.call_interval = 1

        # If exporter is running
        self.running = False

    def _initial_setup(self):
        """ Start a collector registry to send data to node exporter
            and create one object to hold each metric data
        """
        self.registry = CollectorRegistry()

        for name, comment in METRIC_INFO.items():
            self.metric_gauges[name] = Gauge(name, comment, registry=self.registry)

    def start(self):
        """ Starts exporter
        """
        self.running = True
        self._schedule_and_write_data()

    def _schedule_and_write_data(self):
        """ Update all metric data with new values
            Write new data to file
            Schedule next call
        """
        if self.running:
            for metric_name in METRIC_INFO.keys():
                self.metric_gauges[metric_name].set(getattr(self.metrics, metric_name))

            write_to_textfile(self.filepath, self.registry)

            # Schedule next call
            reactor.callLater(
                self.call_interval,
                self._schedule_and_write_data
            )

    def stop(self):
        """ Stops exporter
        """
        self.running = False
