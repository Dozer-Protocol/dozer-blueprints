import socket
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

import twisted.names.client
from twisted.logger import Logger


class PeerDiscovery(ABC):
    """ Base class to implement peer discovery strategies.
    """

    @abstractmethod
    def discover_and_connect(self, connect_to: Callable[[str], None]):
        """ This method must discover the peers and call `connect_to` for each of them.

        :param connect_to: Function which will be called for each discovered peer.
        :type connect_to: function
        """
        raise NotImplementedError


class BootstrapPeerDiscovery(PeerDiscovery):
    """ It implements a bootstrap peer discovery, which receives a static list of peers.
    """
    log = Logger()

    def __init__(self, descriptions: List[str]):
        """
        :param descriptions: Descriptions of peers to connect to.
        """
        super().__init__()
        self.descriptions = descriptions

    def discover_and_connect(self, connect_to: Callable[[str], None]):
        for description in self.descriptions:
            connect_to(description)


class DNSPeerDiscovery(PeerDiscovery):
    """ It implements a DNS peer discovery, which looks for peers in A, AAA, and TXT records.
    """
    log = Logger()

    connect_to: Optional[Callable[[str], None]]

    def __init__(self, hosts: List[str], default_port: int = 40403):
        """
        :param hosts: List of hosts to be queried
        :param default_port: Port number which will be used to connect when only IP address is available.
        """
        self.hosts = hosts
        self.default_port = default_port
        self.connect_to = None

    def discover_and_connect(self, connect_to: Callable[[str], None]):
        self.connect_to = connect_to
        for host in self.hosts:
            self.dns_seed_lookup(host)

    def dns_seed_lookup(self, host: str):
        """ Run a DNS lookup for TXT, A, and AAAA records to discover new peers.
        """
        self.dns_seed_lookup_text(host)
        self.dns_seed_lookup_address(host)
        # self.dns_seed_lookup_ipv6_address(host)

    def dns_seed_lookup_text(self, host: str):
        """ Run a DNS lookup for TXT records to discover new peers.
        """
        x = twisted.names.client.lookupText(host)
        x.addCallback(self.on_dns_seed_found)

    def dns_seed_lookup_address(self, host: str):
        """ Run a DNS lookup for A records to discover new peers.
        """
        x = twisted.names.client.lookupAddress(host)
        x.addCallback(self.on_dns_seed_found_ipv4)

    def dns_seed_lookup_ipv6_address(self, host: str):
        """ Run a DNS lookup for AAAA records to discover new peers.
        """
        x = twisted.names.client.lookupIPV6Address(host)
        x.addCallback(self.on_dns_seed_found_ipv6)

    def on_dns_seed_found(self, results):
        """ Executed only when a new peer is discovered by `dns_seed_lookup_text`.
        """
        answers, _, _ = results
        for x in answers:
            data = x.payload.data
            for txt in data:
                txt = txt.decode('utf-8')
                try:
                    self.log.info('Seed DNS TXT: {txt!r} found', txt=txt)
                    self.connect_to(txt)
                except ValueError:
                    self.log.info('Seed DNS TXT: Error parsing {txt!r}', txt=txt)

    def on_dns_seed_found_ipv4(self, results):
        """ Executed only when a new peer is discovered by `dns_seed_lookup_address`.
        """
        answers, _, _ = results
        for x in answers:
            address = x.payload.address
            host = socket.inet_ntoa(address)
            self.connect_to('tcp:{}:{}'.format(host, self.default_port))
            self.log.info('Seed DNS A: {host!r} found', host=host)

    def on_dns_seed_found_ipv6(self, results):
        """ Executed only when a new peer is discovered by `dns_seed_lookup_ipv6_address`.
        """
        # answers, _, _ = results
        # for x in answers:
        #     address = x.payload.address
        #     host = socket.inet_ntop(socket.AF_INET6, address)
        raise NotImplementedError
