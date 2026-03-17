#
# Webhook models — WebhookEndpoint, WebhookDelivery
#
from btpay.orm.model import MemModel, BaseMixin
from btpay.orm.columns import (
    Text, Integer, Boolean, DateTimeColumn, JsonColumn, TagsColumn,
)


class WebhookEndpoint(BaseMixin, MemModel):
    org_id      = Integer(index=True)
    url         = Text(required=True)
    secret      = Text()                    # HMAC-SHA256 signing secret
    events      = TagsColumn()              # subscribed events
    is_active   = Boolean(default=True)
    description = Text()

    @property
    def subscribed_events(self):
        return set(self.events or [])


class WebhookDelivery(BaseMixin, MemModel):
    endpoint_id     = Integer(index=True)
    event           = Text(index=True)
    payload         = JsonColumn()
    response_status = Integer(default=0)
    response_body   = Text()
    attempts        = Integer(default=0)
    last_attempt_at = DateTimeColumn(default=0)
    delivered       = Boolean(default=False)
    error           = Text()

# EOF
