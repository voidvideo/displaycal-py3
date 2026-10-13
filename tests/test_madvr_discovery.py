"""Discovery/selection behavior, using simulated peers rather than a live display."""

import socket
import threading
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from DisplayCAL import config, madvr
from DisplayCAL import worker as worker_module
from DisplayCAL.worker import Worker


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "CFG", deepcopy(config.CFG))
    config.initcfg()


@pytest.fixture
def peer(monkeypatch):
    monkeypatch.setattr(madvr, "get_network_addr", lambda: "127.0.0.1")
    client = madvr.MadTPGNet()
    client.listening = True
    monkeypatch.setattr(client, "announce", MagicMock())
    monkeypatch.setattr(client, "_send", MagicMock(return_value=True))
    yield client
    client._reset()
    client.shutdown()


def add_generator(peer, host, port=60562, pid=100, confirmed=True):
    """Model two processes that can legitimately share the same PID on different hosts."""
    addr = (host, port)
    conn = MagicMock(spec=socket.socket)
    conn.getpeername.return_value = addr
    peer._client_sockets[addr] = conn
    peer.clients[addr] = {
        "component": b"madTPG",
        "processId": pid,
        "module": 1,
        "instance": 0,
        "confirmed": confirmed,
        "computerName": host,
        "exeDescr": "VoidVideo calibration display",
        "mvrVersion": madvr.MIN_VERSION,
    }
    return conn


def test_discovery_lists_confirmed_generators_without_activating(peer):
    add_generator(peer, "127.0.0.2")
    add_generator(peer, "127.0.0.3")
    add_generator(peer, "127.0.0.4", confirmed=False)
    add_generator(peer, "127.0.0.5")
    peer.clients[("127.0.0.5", 60562)]["component"] = b""

    targets = peer.discovered_instances()

    assert len(targets) == 2
    assert targets[0].key != targets[1].key
    assert targets[0].label == "VoidVideo calibration display — 127.0.0.2"
    assert peer._client_socket is None
    peer._send.assert_not_called()


def test_activation_targets_selected_generator_only(peer):
    first = add_generator(peer, "127.0.0.2")
    selected = add_generator(peer, "127.0.0.3")
    target = peer.discovered_instances()[1]

    assert peer.connect_to_instance(target)

    peer._send.assert_called_once_with(selected, "StartTestPattern")
    assert peer._client_socket is selected
    assert peer._client_socket is not first


@pytest.mark.parametrize("replacement", [False, True])
def test_lost_instance_never_falls_back_to_another_process(peer, replacement):
    add_generator(peer, "127.0.0.2")
    target = peer.discovered_instances()[0]
    peer.clients.clear()
    if replacement:
        add_generator(peer, "127.0.0.2", pid=101)
    else:
        add_generator(peer, "127.0.0.3")

    assert not peer.connect_to_instance(target, timeout=20)
    peer._send.assert_not_called()


def test_reconnected_process_can_use_a_new_connect_back_port(peer):
    add_generator(peer, "127.0.0.2", port=50000)
    target = peer.discovered_instances()[0]
    peer.clients.clear()
    selected = add_generator(peer, "127.0.0.2", port=50001)

    assert peer.connect_to_instance(target)
    peer._send.assert_called_once_with(selected, "StartTestPattern")


def test_confirmation_does_not_deduplicate_processes_on_different_hosts(
    peer, monkeypatch
):
    add_generator(peer, "127.0.0.2")
    conn = add_generator(peer, "127.0.0.3", confirmed=False)
    addr = conn.getpeername()
    record = peer.clients.pop(addr)
    record.pop("confirmed")
    monkeypatch.setattr(peer, "_is_master", lambda conn: True)

    assert not peer._process_new_client(conn, addr, {}, record)
    assert len(peer.discovered_instances()) == 2


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_display_choices_preserve_canonical_indices_and_lost_selection(
    peer, monkeypatch, platform
):
    config.initcfg()
    worker = Worker()
    worker.displays = ["Monitor", "madVR", "Resolve"]
    config.setcfg("displays", worker.displays)
    config.setcfg("display.number", 1)
    worker._madtpg_discovery = peer
    add_generator(peer, "127.0.0.2")
    add_generator(peer, "127.0.0.3")
    monkeypatch.setattr(worker_module.sys, "platform", platform)
    rows = worker.display_choices()

    assert any(row[0] == "madVR" for row in rows) is (platform == "win32")
    assert worker.select_display_choice(rows[-1]) == 1
    assert config.get_display_name(None, True) == "madVR"
    assert worker.displays == ["Monitor", "madVR", "Resolve"]
    assert worker.selected_display_choice(rows) == len(rows) - 1
    key = worker.madtpg_target.key
    peer.clients.pop(("127.0.0.3", 60562))
    rows = worker.display_choices()
    assert rows[worker.selected_display_choice(rows)][2].key == key

    resolve = next(row for row in rows if row[0] == "Resolve")
    # Reloading settings can change the canonical display before the UI handler runs.
    config.setcfg("display.number", 3)
    assert rows[worker.selected_display_choice(rows)] == resolve
    worker.select_display_choice(resolve)
    assert config.get_display_name() == "Resolve"
    assert worker.madtpg_target is None
    assert rows[worker.selected_display_choice(rows)] == resolve


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_hidden_generic_selection_does_not_choose_an_arbitrary_peer(
    peer, monkeypatch, platform
):
    worker = Worker()
    worker.displays = ["Monitor", "madVR", "Resolve"]
    worker._madtpg_discovery = peer
    add_generator(peer, "127.0.0.2")
    config.setcfg("display.number", 2)
    monkeypatch.setattr(worker_module.sys, "platform", platform)

    assert worker.selected_display_choice(worker.display_choices()) == -1
    assert not worker.madtpg_connect()
    peer._send.assert_not_called()


def test_windows_generic_selection_switches_back_to_native(peer, monkeypatch):
    # The visible choice defines the transport, even with an old Python preference.
    config.setcfg("madtpg.native", 0)
    worker = Worker()
    worker.displays = ["Monitor", "madVR"]
    worker._madtpg_discovery = peer
    worker.log = MagicMock()
    add_generator(peer, "127.0.0.2")
    target = peer.discovered_instances()[0]
    native = MagicMock()
    native.get_version.return_value = madvr.MIN_VERSION
    factory = MagicMock(return_value=native)
    monkeypatch.setattr(madvr, "MadTPG", factory)
    monkeypatch.setattr(worker_module.sys, "platform", "win32")
    choices = worker.display_choices()
    generic = next(row for row in choices if row[0] == "madVR")
    named = next(row for row in choices if row[2] == target)

    worker.select_display_choice(named)
    assert worker.madtpg_connect()
    assert worker.madtpg is peer
    factory.assert_not_called()

    worker.select_display_choice(generic)
    assert not worker.uses_madtpg_network()
    assert worker.madtpg_connect()
    assert worker.madtpg is native
    assert peer._client_socket is None
    factory.assert_called_once_with()
    native.connect.assert_called_once_with(
        method3=madvr.CM_StartLocalInstance, timeout3=3000
    )

    worker.select_display_choice(named)
    assert worker.uses_madtpg_network()
    assert worker.madtpg_connect()
    assert worker.madtpg is peer
    native.disconnect.assert_called_once_with()


def test_windows_explicit_target_uses_network_instead_of_native(peer, monkeypatch):
    config.initcfg()
    config.setcfg("madtpg.native", 1)
    worker = Worker()
    worker._madtpg_discovery = peer
    conn = add_generator(peer, "127.0.0.2")
    worker.madtpg_target = peer.discovered_instances()[0]
    worker.log = MagicMock()
    native = MagicMock()
    monkeypatch.setattr(worker_module.sys, "platform", "win32")
    monkeypatch.setattr(madvr, "MadTPG", native)

    assert worker.madtpg_connect()
    assert worker.madtpg is peer
    assert peer._client_socket is conn
    native.assert_not_called()


def test_worker_background_discovery_can_stop_without_activation(peer, monkeypatch):
    worker = Worker()
    worker.displays = ["madVR"]
    worker._madtpg_discovery = peer
    announced = threading.Event()
    monkeypatch.setattr(peer, "announce", announced.set)

    worker.start_madtpg_discovery()
    try:
        assert announced.wait(2)
    finally:
        worker.stop_madtpg_discovery()

    assert worker._madtpg_discovery_thread is None
    assert not peer.listening
    peer._send.assert_not_called()
