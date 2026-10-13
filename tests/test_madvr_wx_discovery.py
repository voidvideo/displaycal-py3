"""Exercise the wx display selector with real widgets and simulated discovery."""

import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import wx

from DisplayCAL import config
from DisplayCAL import localization as lang
from DisplayCAL.display_cal import MainFrame
from DisplayCAL.madvr import MadTPGInstance
from DisplayCAL.worker import Worker


@pytest.fixture
def selector(monkeypatch):
    monkeypatch.setattr(config, "CFG", deepcopy(config.CFG))
    config.initcfg()
    app = wx.GetApp() or wx.App()
    frame = wx.Frame(None)
    choice = wx.Choice(frame)
    worker = Worker()
    worker.displays = ["Monitor", "madVR", "Resolve"]
    config.setcfg("displays", worker.displays)
    config.setcfg("display.number", 1)
    view = MagicMock()
    view.worker = worker
    view.display_ctrl = choice
    view.display_lut_link_ctrl.IsShown.return_value = False
    view.IsShownOnScreen.return_value = False
    view.reportframe = view.lut3dframe = None
    view._display_choices = []
    choice.Bind(
        wx.EVT_CHOICE,
        lambda event: MainFrame.display_ctrl_handler(
            view, event, load_lut=False, update_ccmx_items=False
        ),
    )
    yield view
    frame.Destroy()
    app.ProcessPendingEvents()


def choose(view, index):
    view.display_ctrl.SetSelection(index)
    event = wx.CommandEvent(wx.EVT_CHOICE.typeId, view.display_ctrl.GetId())
    event.SetEventObject(view.display_ctrl)
    view.display_ctrl.GetEventHandler().ProcessEvent(event)


def test_wx_discovery_selection_survives_refresh_and_disconnect(selector):
    first = MadTPGInstance(("127.0.0.2", 60562), 100, 1, 0, "VoidVideo")
    second = MadTPGInstance(("127.0.0.3", 60562), 100, 1, 0, "VoidVideo")
    targets = [first, second]
    selector.worker._madtpg_discovery = SimpleNamespace(
        discovered_instances=lambda: targets
    )
    MainFrame._refresh_madtpg_displays(selector, None)
    assert config.getcfg("display.number") == 1
    assert selector.display_ctrl.GetCount() == (5 if sys.platform == "win32" else 4)
    assert (selector.display_ctrl.FindString("madVR") >= 0) is (sys.platform == "win32")

    choose(selector, selector.display_ctrl.FindString(second.label))
    assert selector.worker.madtpg_target == second
    assert config.getcfg("display.number") == 2
    assert config.get_display_name(None, True) == "madVR"

    targets.reverse()
    MainFrame._refresh_madtpg_displays(selector, None)
    assert selector.display_ctrl.GetStringSelection() == second.label
    targets.remove(second)
    MainFrame._refresh_madtpg_displays(selector, None)
    assert second.label in selector.display_ctrl.GetStringSelection()
    assert lang.getstr("not_connected") in selector.display_ctrl.GetStringSelection()
    assert selector.worker.madtpg_target == second

    choose(selector, selector.display_ctrl.FindString("Resolve"))
    assert selector.worker.madtpg_target is None
    assert config.getcfg("display.number") == 3
