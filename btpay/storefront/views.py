#
# Storefront admin views — create, edit, manage storefronts and items
#
import logging
from decimal import Decimal, InvalidOperation
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, g, current_app,
)

from btpay.auth.decorators import login_required, role_required, csrf_protect

log = logging.getLogger(__name__)

storefront_bp = Blueprint('storefronts', __name__, url_prefix='/storefronts')


def _csrf_check():
    from btpay.security.csrf import validate_csrf_token
    token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token', '')
    cookie_name = current_app.config.get('AUTH_COOKIE_NAME', 'btpay_session')
    session_token = request.cookies.get(cookie_name, '')
    secret = current_app.config.get('SECRET_KEY', '')
    if not validate_csrf_token(session_token, token, secret):
        from flask import abort
        abort(403)


# ---- List ----

@storefront_bp.route('/')
@login_required
@role_required('admin')
def list_storefronts():
    from btpay.storefront.models import Storefront
    storefronts = Storefront.query.filter(org_id=g.org.id).all()
    return render_template('storefronts/list.html', storefronts=storefronts, org=g.org)


# ---- Create ----

@storefront_bp.route('/create', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_storefront():
    from btpay.storefront.models import Storefront

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        title = form.get('title', '').strip()
        if not title:
            flash('Title is required', 'error')
            return redirect(url_for('storefronts.create_storefront'))

        slug = Storefront.make_slug(title)
        storefront_type = form.get('storefront_type', 'store')
        if storefront_type not in ('store', 'donation'):
            storefront_type = 'store'

        sf = Storefront(
            org_id=g.org.id,
            slug=slug,
            title=title,
            description=form.get('description', '').strip(),
            storefront_type=storefront_type,
            currency=form.get('currency', g.org.default_currency or 'USD'),
            button_text=form.get('button_text', '').strip() or ('Donate' if storefront_type == 'donation' else 'Buy Now'),
            hero_image_url=form.get('hero_image_url', '').strip(),
            brand_color=Storefront.sanitize_color(form.get('brand_color', '')),
            require_email=form.get('require_email') == '1',
            require_name=form.get('require_name') == '1',
            success_message=form.get('success_message', '').strip() or 'Thank you!',
            redirect_url=form.get('redirect_url', '').strip(),
        )

        # Donation-specific
        if storefront_type == 'donation':
            presets_raw = form.get('donation_presets', '').strip()
            if presets_raw:
                try:
                    presets = [int(x.strip()) for x in presets_raw.split(',') if x.strip()]
                    sf.donation_presets = presets
                except ValueError:
                    flash('Donation presets must be comma-separated numbers', 'error')
                    return redirect(url_for('storefronts.create_storefront'))
            else:
                sf.donation_presets = [5, 10, 25, 50, 100]
            sf.donation_allow_custom = form.get('donation_allow_custom') == '1'
            goal = form.get('donation_goal_amount', '').strip()
            if goal:
                try:
                    sf.donation_goal_amount = Decimal(goal)
                except InvalidOperation:
                    pass
            sf.donation_goal_label = form.get('donation_goal_label', '').strip()

        sf.save()
        flash('Storefront created: %s' % title, 'success')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    return render_template('storefronts/create.html', org=g.org)


# ---- Edit ----

@storefront_bp.route('/<int:storefront_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_storefront(storefront_id):
    from btpay.storefront.models import Storefront

    sf = Storefront.get(storefront_id)
    if sf is None or sf.org_id != g.org.id:
        flash('Storefront not found', 'error')
        return redirect(url_for('storefronts.list_storefronts'))

    if request.method == 'POST':
        _csrf_check()
        form = request.form

        sf.title = form.get('title', sf.title).strip()
        sf.description = form.get('description', '').strip()
        sf.currency = form.get('currency', sf.currency)
        sf.is_active = form.get('is_active') == '1'
        sf.button_text = form.get('button_text', '').strip() or sf.button_text
        sf.hero_image_url = form.get('hero_image_url', '').strip()
        sf.brand_color = Storefront.sanitize_color(form.get('brand_color', ''))
        sf.require_email = form.get('require_email') == '1'
        sf.require_name = form.get('require_name') == '1'
        sf.success_message = form.get('success_message', '').strip() or 'Thank you!'
        sf.redirect_url = form.get('redirect_url', '').strip()

        if sf.is_donation:
            presets_raw = form.get('donation_presets', '').strip()
            if presets_raw:
                try:
                    sf.donation_presets = [int(x.strip()) for x in presets_raw.split(',') if x.strip()]
                except ValueError:
                    flash('Donation presets must be comma-separated numbers', 'error')
            sf.donation_allow_custom = form.get('donation_allow_custom') == '1'
            goal = form.get('donation_goal_amount', '').strip()
            if goal:
                try:
                    sf.donation_goal_amount = Decimal(goal)
                except InvalidOperation:
                    pass
            sf.donation_goal_label = form.get('donation_goal_label', '').strip()

        sf.save()
        flash('Storefront updated', 'success')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    items = sf.items
    return render_template('storefronts/edit.html', sf=sf, items=items, org=g.org)


# ---- Delete storefront ----

@storefront_bp.route('/<int:storefront_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def delete_storefront(storefront_id):
    from btpay.storefront.models import Storefront, StorefrontItem

    sf = Storefront.get(storefront_id)
    if sf is None or sf.org_id != g.org.id:
        flash('Storefront not found', 'error')
        return redirect(url_for('storefronts.list_storefronts'))

    # Delete items first
    for item in sf.items:
        item.delete()

    title = sf.title
    sf.delete()
    flash('Storefront deleted: %s' % title, 'success')
    return redirect(url_for('storefronts.list_storefronts'))


# ---- Item management ----

@storefront_bp.route('/<int:storefront_id>/items/add', methods=['POST'])
@login_required
@role_required('admin')
def add_item(storefront_id):
    from btpay.storefront.models import Storefront, StorefrontItem

    sf = Storefront.get(storefront_id)
    if sf is None or sf.org_id != g.org.id:
        flash('Storefront not found', 'error')
        return redirect(url_for('storefronts.list_storefronts'))

    _csrf_check()
    form = request.form

    title = form.get('title', '').strip()
    if not title:
        flash('Item title is required', 'error')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    price_str = form.get('price', '0').strip()
    try:
        price = Decimal(price_str) if price_str else Decimal('0')
    except InvalidOperation:
        flash('Invalid price', 'error')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    # Get current max sort_order
    existing = sf.items
    max_order = max((i.sort_order for i in existing), default=-1)

    item = StorefrontItem(
        storefront_id=sf.id,
        title=title,
        description=form.get('description', '').strip(),
        price=price,
        image_url=form.get('image_url', '').strip(),
        category=form.get('category', '').strip(),
        sort_order=max_order + 1,
    )
    item.save()

    flash('Item added: %s' % title, 'success')
    return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))


@storefront_bp.route('/<int:storefront_id>/items/<int:item_id>/edit', methods=['POST'])
@login_required
@role_required('admin')
def edit_item(storefront_id, item_id):
    from btpay.storefront.models import Storefront, StorefrontItem

    sf = Storefront.get(storefront_id)
    if sf is None or sf.org_id != g.org.id:
        flash('Storefront not found', 'error')
        return redirect(url_for('storefronts.list_storefronts'))

    _csrf_check()
    item = StorefrontItem.get(item_id)
    if item is None or item.storefront_id != sf.id:
        flash('Item not found', 'error')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    form = request.form
    item.title = form.get('title', item.title).strip()
    item.description = form.get('description', '').strip()
    item.image_url = form.get('image_url', '').strip()
    item.category = form.get('category', '').strip()
    item.is_active = form.get('is_active') == '1'

    price_str = form.get('price', '').strip()
    if price_str:
        try:
            item.price = Decimal(price_str)
        except InvalidOperation:
            flash('Invalid price', 'error')

    item.save()
    flash('Item updated', 'success')
    return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))


@storefront_bp.route('/<int:storefront_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
@csrf_protect
def delete_item(storefront_id, item_id):
    from btpay.storefront.models import Storefront, StorefrontItem

    sf = Storefront.get(storefront_id)
    if sf is None or sf.org_id != g.org.id:
        flash('Storefront not found', 'error')
        return redirect(url_for('storefronts.list_storefronts'))

    item = StorefrontItem.get(item_id)
    if item is None or item.storefront_id != sf.id:
        flash('Item not found', 'error')
        return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

    title = item.title
    item.delete()
    flash('Item removed: %s' % title, 'success')
    return redirect(url_for('storefronts.edit_storefront', storefront_id=sf.id))

# EOF
