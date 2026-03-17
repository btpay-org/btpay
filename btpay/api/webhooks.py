#
# Webhook event dispatch
#
# HMAC-SHA256 signed payloads, exponential backoff retry, delivery tracking.
#
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
from urllib.parse import urlparse

import requests

from btpay.chrono import NOW

log = logging.getLogger(__name__)


class WebhookDispatcher:
    '''
    Dispatch webhook events to registered endpoints.

    Usage:
        dispatcher = WebhookDispatcher(retry_delays=[60, 300, 900])
        dispatcher.dispatch('invoice.paid', {'invoice_id': 42}, org_id=1)
    '''

    def __init__(self, retry_delays=None, _max_sleep=None):
        self.retry_delays = [60, 300, 900, 3600, 7200] if retry_delays is None else retry_delays
        self._max_sleep = _max_sleep  # None = no cap; set to 0 in tests
        self._retry_thread = None
        self._stop_event = threading.Event()

    def dispatch(self, event, data, org_id):
        '''
        Send event to all matching webhook endpoints for this org.
        Delivery happens in background threads.
        '''
        from btpay.api.webhook_models import WebhookEndpoint

        endpoints = WebhookEndpoint.query.filter(
            org_id=org_id, is_active=True
        ).all()

        for ep in endpoints:
            if event in ep.subscribed_events or '*' in ep.subscribed_events:
                t = threading.Thread(
                    target=self._deliver,
                    args=(ep, event, data),
                    daemon=True,
                )
                t.start()

    def _deliver(self, endpoint, event, data):
        '''Deliver a webhook to a single endpoint.'''
        from btpay.api.webhook_models import WebhookDelivery

        payload = {
            'event': event,
            'data': data,
            'timestamp': str(NOW()),
        }
        payload_json = json.dumps(payload, default=str)

        # Create delivery record
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event=event,
            payload=payload,
        )
        delivery.save()

        # Sign payload
        signature = self._sign(payload_json, endpoint.secret or '')

        # Attempt delivery
        success = self._attempt(endpoint.url, payload_json, signature, delivery)

        if not success:
            # Schedule retries
            self._schedule_retries(endpoint, delivery, payload_json)

    @staticmethod
    def _resolve_and_validate(hostname, port):
        '''Resolve hostname and validate all IPs are public. Returns resolved IP string.'''
        addr_info = socket.getaddrinfo(hostname, port or 443)
        resolved_ip = None
        for family, type_, proto, canonname, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                raise ValueError('resolves to private IP %s' % ip)
            if resolved_ip is None:
                resolved_ip = str(ip)
        return resolved_ip

    def _attempt(self, url, payload_json, signature, delivery):
        '''Make a single delivery attempt. Returns True on success.'''
        # Resolve DNS and validate all addresses are public
        parsed = urlparse(url)
        hostname = parsed.hostname
        try:
            self._resolve_and_validate(hostname, parsed.port)
        except ValueError as e:
            delivery.error = 'SSRF blocked: %s %s' % (hostname, e)
            delivery.save()
            log.warning('Webhook SSRF blocked: %s: %s', url, e)
            return False
        except socket.gaierror as e:
            delivery.error = 'DNS resolution failed: %s' % e
            delivery.save()
            log.warning('Webhook DNS failed: %s: %s', url, e)
            return False

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'BTPay-Webhook/1.0',
            'X-BTPay-Signature': signature,
            'X-BTPay-Event': delivery.event,
        }

        delivery.attempts += 1
        delivery.last_attempt_at = NOW()

        try:
            resp = requests.post(
                url, data=payload_json, headers=headers,
                timeout=30, allow_redirects=False,
            )
            delivery.response_status = resp.status_code
            delivery.response_body = resp.text[:1024]  # truncate

            if 200 <= resp.status_code < 300:
                delivery.delivered = True
                delivery.save()
                log.info('Webhook delivered: %s -> %s (status=%d)',
                         delivery.event, url, resp.status_code)
                return True
            else:
                delivery.error = 'HTTP %d' % resp.status_code
                delivery.save()
                log.warning('Webhook failed: %s -> %s (status=%d)',
                            delivery.event, url, resp.status_code)
                return False

        except Exception as e:
            delivery.error = str(e)[:512]
            delivery.save()
            log.warning('Webhook error: %s -> %s: %s', delivery.event, url, e)
            return False

    def _schedule_retries(self, endpoint, delivery, payload_json):
        '''Retry delivery with exponential backoff.'''
        signature = self._sign(payload_json, endpoint.secret or '')

        for delay in self.retry_delays:
            if delivery.delivered:
                break
            if delivery.attempts > len(self.retry_delays):
                break

            sleep_time = delay if self._max_sleep is None else min(delay, self._max_sleep)
            if sleep_time > 0:
                time.sleep(sleep_time)
            success = self._attempt(endpoint.url, payload_json, signature, delivery)
            if success:
                break

        if not delivery.delivered:
            log.error('Webhook permanently failed after %d attempts: %s -> %s',
                      delivery.attempts, delivery.event, endpoint.url)

    @staticmethod
    def _sign(payload_json, secret):
        '''HMAC-SHA256 signature of the payload.'''
        if not secret:
            return ''
        return hmac.new(
            secret.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    @staticmethod
    def verify_signature(payload_json, signature, secret):
        '''Verify an HMAC-SHA256 webhook signature.'''
        expected = hmac.new(
            secret.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

# EOF
