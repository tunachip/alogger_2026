from __future__ import annotations

from .ai import AIPopupMixin
from .base import PopupBaseMixin
from .channel import ChannelPopupMixin
from .command import CommandPopupMixin
from .ingest import IngestPopupMixin
from .jobs import JobsPopupMixin
from .navigation import NavigationPopupMixin
from .search import SearchPopupMixin
from .settings import SettingsPopupMixin
from .subscriptions import SubscriptionsPopupMixin


class PopupMixin(
    JobsPopupMixin,
    SubscriptionsPopupMixin,
    ChannelPopupMixin,
    IngestPopupMixin,
    SearchPopupMixin,
    SettingsPopupMixin,
    NavigationPopupMixin,
    AIPopupMixin,
    CommandPopupMixin,
    PopupBaseMixin,
):
    pass
