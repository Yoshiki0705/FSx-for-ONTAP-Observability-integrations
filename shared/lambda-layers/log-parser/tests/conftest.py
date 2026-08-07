"""Pytest configuration and shared fixtures for the log-parser layer tests."""

import sys
from pathlib import Path

import pytest

# Add the layer's python directory to sys.path so tests can import fsxn_log_parser
_layer_python_dir = str(Path(__file__).parent.parent / "python")
if _layer_python_dir not in sys.path:
    sys.path.insert(0, _layer_python_dir)


@pytest.fixture
def namespaced_ontap_xml() -> str:
    """Two-event ONTAP XML audit log in the Windows Event Log schema.

    This is what `vserver audit create -format xml` actually produces: every
    element carries the Windows Event Log namespace.
    """
    return """<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="2026-08-07T04:00:00Z"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\jdoe</Data>
    <Data Name="ObjectName">/vol/data/first.txt</Data>
    <Data Name="ObjectType">ReadData</Data>
    <Data Name="IpAddress">198.51.100.10</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4660</EventID>
    <TimeCreated SystemTime="2026-08-07T04:00:01Z"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\jdoe</Data>
    <Data Name="ObjectName">/vol/data/second.docx</Data>
    <Data Name="ObjectType">Delete</Data>
    <Data Name="IpAddress">198.51.100.10</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
</Events>"""


@pytest.fixture
def plain_ontap_xml() -> str:
    """Two-event ONTAP XML audit log without namespaces."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event>
  <System><EventID>4663</EventID><TimeCreated SystemTime="2026-08-07T04:00:00Z"/><Computer>svm1</Computer></System>
  <EventData><Data Name="SubjectUserName">u1</Data><Data Name="ObjectName">/a.txt</Data></EventData>
</Event>
<Event>
  <System><EventID>4660</EventID><TimeCreated SystemTime="2026-08-07T04:00:01Z"/><Computer>svm1</Computer></System>
  <EventData><Data Name="SubjectUserName">u2</Data><Data Name="ObjectName">/b.txt</Data></EventData>
</Event>
</Events>"""
