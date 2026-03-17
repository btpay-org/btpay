#
# Email service — SMTP and Mailgun sending
#
# Supports per-org config override: SMTP (smtplib) or Mailgun (HTTP API).
#
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)


class EmailService:
    '''
    SMTP email sender.

    Usage:
        svc = EmailService(smtp_config)
        svc.send(to='customer@example.com', subject='Invoice', html=body)
    '''

    def __init__(self, smtp_config=None):
        self.config = smtp_config or {}

    @classmethod
    def for_org(cls, org, app_config):
        '''
        Create EmailService with per-org config.
        Picks Mailgun if mailgun_api_key is set, otherwise SMTP.
        '''
        org_smtp = getattr(org, 'smtp_config', None) or {}

        # Check for Mailgun config first
        if org_smtp.get('mailgun_api_key') and org_smtp.get('mailgun_domain'):
            return MailgunEmailService(org_smtp)

        if org_smtp and org_smtp.get('server'):
            return cls(org_smtp)
        return cls(app_config.get('SMTP_CONFIG', {}))

    def is_configured(self):
        '''Check if SMTP is configured (server is set).'''
        server = self._get('server', '')
        return bool(server)

    def send(self, to, subject, html, text=None, from_address=None,
             reply_to=None, cc=None, bcc=None):
        '''
        Send an email.
        Returns True on success, False on failure.
        '''
        if not self.is_configured():
            log.warning('SMTP not configured, skipping email to %s', to)
            return False

        server = self._get('server', '')
        port = int(self._get('port', 587))
        username = self._get('username', '')
        password = self._get('password', '')
        use_tls = self._get('use_tls', True)
        sender = from_address or self._get('from_address', 'noreply@localhost')

        # Sanitize all header values to prevent header injection
        def _sanitize_header(val):
            if isinstance(val, str):
                return val.replace('\r', '').replace('\n', '')
            return val

        sender = _sanitize_header(sender)
        subject = _sanitize_header(subject)

        # Build message
        msg = MIMEMultipart('alternative')
        msg['From'] = sender
        msg['To'] = _sanitize_header(to if isinstance(to, str) else ', '.join(to))
        msg['Subject'] = subject

        if reply_to:
            msg['Reply-To'] = _sanitize_header(reply_to)
        if cc:
            msg['Cc'] = _sanitize_header(cc if isinstance(cc, str) else ', '.join(cc))

        # Attach text part first (fallback), then HTML
        if text:
            msg.attach(MIMEText(text, 'plain', 'utf-8'))
        msg.attach(MIMEText(html, 'html', 'utf-8'))

        # Build recipient list
        recipients = [to] if isinstance(to, str) else list(to)
        if cc:
            cc_list = [cc] if isinstance(cc, str) else list(cc)
            recipients.extend(cc_list)
        if bcc:
            bcc_list = [bcc] if isinstance(bcc, str) else list(bcc)
            recipients.extend(bcc_list)

        try:
            if port == 465:
                # SSL from the start
                smtp = smtplib.SMTP_SSL(server, port, timeout=30)
            else:
                smtp = smtplib.SMTP(server, port, timeout=30)
                if use_tls:
                    smtp.starttls()

            if username and password:
                smtp.login(username, password)

            smtp.sendmail(sender, recipients, msg.as_string())
            smtp.quit()

            log.info('Email sent to %s: %s', to, subject)
            return True

        except smtplib.SMTPException as e:
            log.error('SMTP error sending to %s: %s', to, e)
            return False
        except Exception as e:
            log.error('Email error sending to %s: %s', to, e)
            return False

    def _notification_enabled(self, org, key):
        '''Check if a notification type is enabled for the org.'''
        prefs = getattr(org, 'notification_prefs', None) or {}
        return prefs.get(key, True)  # default to enabled

    def send_invoice_created(self, invoice, org, checkout_url=''):
        '''Send invoice created notification to customer.'''
        if not self._notification_enabled(org, 'invoice_created'):
            return False
        from btpay.email.templates import render_invoice_created

        if not invoice.customer_email:
            return False

        html = render_invoice_created(invoice, org, checkout_url)
        subject = 'Invoice %s from %s' % (invoice.invoice_number, org.name)

        return self.send(
            to=invoice.customer_email,
            subject=subject,
            html=html,
            from_address=self._get('from_address', 'noreply@localhost'),
        )

    def send_payment_received(self, invoice, payment, org):
        '''Send payment received notification.'''
        if not self._notification_enabled(org, 'payment_received'):
            return False
        from btpay.email.templates import render_payment_received

        if not invoice.customer_email:
            return False

        html = render_payment_received(invoice, payment, org)
        subject = 'Payment received for %s' % invoice.invoice_number

        return self.send(
            to=invoice.customer_email,
            subject=subject,
            html=html,
        )

    def send_payment_confirmed(self, invoice, payment, org):
        '''Send payment confirmed notification.'''
        if not self._notification_enabled(org, 'payment_confirmed'):
            return False
        from btpay.email.templates import render_payment_confirmed

        if not invoice.customer_email:
            return False

        html = render_payment_confirmed(invoice, payment, org)
        subject = 'Payment confirmed for %s' % invoice.invoice_number

        return self.send(
            to=invoice.customer_email,
            subject=subject,
            html=html,
        )

    def _get(self, key, default=None):
        '''Get config value — works with dict or DictObj.'''
        if hasattr(self.config, key):
            return getattr(self.config, key, default) or default
        if isinstance(self.config, dict):
            return self.config.get(key, default) or default
        return default


class MailgunEmailService(EmailService):
    '''
    Email sender using Mailgun HTTP API.

    Config keys:
        mailgun_api_key   — Mailgun API key (key-xxx...)
        mailgun_domain    — Sending domain (mg.example.com)
        mailgun_region    — 'us' (default) or 'eu'
        from_address      — Sender address
        from_name         — Sender display name
    '''

    # Base URLs per region
    _BASE_URLS = {
        'us': 'https://api.mailgun.net/v3',
        'eu': 'https://api.eu.mailgun.net/v3',
    }

    def __init__(self, config=None):
        super().__init__(config)

    def is_configured(self):
        api_key = self._get('mailgun_api_key', '')
        domain = self._get('mailgun_domain', '')
        return bool(api_key and domain)

    def send(self, to, subject, html, text=None, from_address=None,
             reply_to=None, cc=None, bcc=None):
        if not self.is_configured():
            log.warning('Mailgun not configured, skipping email to %s', to)
            return False

        import urllib.request
        import urllib.parse
        import base64

        api_key = self._get('mailgun_api_key', '')
        domain = self._get('mailgun_domain', '')
        region = self._get('mailgun_region', 'us')

        base_url = self._BASE_URLS.get(region, self._BASE_URLS['us'])
        url = '%s/%s/messages' % (base_url, domain)

        from_name = self._get('from_name', '')
        sender = from_address or self._get('from_address', 'noreply@%s' % domain)
        if from_name:
            sender = '%s <%s>' % (from_name, sender)

        data = {
            'from': sender,
            'to': to if isinstance(to, str) else ', '.join(to),
            'subject': subject,
            'html': html,
        }

        if text:
            data['text'] = text
        if reply_to:
            data['h:Reply-To'] = reply_to
        if cc:
            data['cc'] = cc if isinstance(cc, str) else ', '.join(cc)
        if bcc:
            data['bcc'] = bcc if isinstance(bcc, str) else ', '.join(bcc)

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        auth = base64.b64encode(('api:%s' % api_key).encode()).decode()

        req = urllib.request.Request(url, data=encoded_data, method='POST')
        req.add_header('Authorization', 'Basic %s' % auth)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                log.info('Mailgun email sent to %s: %s (status %d)',
                         to, subject, resp.status)
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            log.error('Mailgun HTTP error %d sending to %s: %s', e.code, to, body)
            return False
        except Exception as e:
            log.error('Mailgun error sending to %s: %s', to, e)
            return False

# EOF
