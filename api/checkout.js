// Serverless function (Vercel): creates a Stripe Checkout page for one configured seat cover.
// Needs the environment variable CACTUS_STRIPE_SECRET_KEY (set it in the hosting dashboard, never in the code).
// Falls back to STRIPE_SECRET_KEY, which Vercel's Stripe integration may create and lock to its own sandbox.
// Optional: SITE_URL (e.g. https://seats.cactusdecal.com) for the return links after payment.
import { PRICING, validate, quote, describe } from '../shop.mjs';

// Stripe's API takes form-encoded nested fields: line_items[0][price_data][currency]=aed …
function encode(obj, prefix = '', out = new URLSearchParams()) {
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (typeof v === 'object') encode(v, key, out); else out.append(key, String(v));
  }
  return out;
}

export default async function handler(req, res) {
  if (req.method !== 'POST') { res.setHeader('Allow', 'POST'); return res.status(405).json({ error: 'Method not allowed' }); }
  const key = process.env.CACTUS_STRIPE_SECRET_KEY || process.env.STRIPE_SECRET_KEY;
  if (!key) return res.status(503).json({ error: 'Online payment is not set up yet.' });

  let order;
  try { order = validate(typeof req.body === 'string' ? JSON.parse(req.body) : req.body); }
  catch (e) { return res.status(400).json({ error: e.message }); }

  const q = quote(order), currency = PRICING.currency.toLowerCase(), cents = v => Math.round(v * 100);
  const spec = describe(order);
  const metadata = Object.fromEntries(spec.map(([k, v]) => [k.toLowerCase().replace(/[^a-z]+/g, '_').slice(0, 40), v.slice(0, 500)]));
  if (order.link) metadata.design_link = order.link;
  const origin = (process.env.SITE_URL || `https://${req.headers.host}`).replace(/\/$/, '');

  const line_items = [{
    quantity: order.qty,
    price_data: {
      currency, unit_amount: cents(q.seat),
      product_data: {
        name: `GUTS Rib Seat Cover — ${order.make} ${order.model} ${order.year}`,
        description: spec.slice(1, 8).map(([k, v]) => `${k}: ${v}`).join(' · ').slice(0, 1000),
      },
    },
  }];
  if (q.cleaner) line_items.push({ quantity: order.qty, price_data: { currency, unit_amount: cents(q.cleaner), product_data: { name: OPTIONS_CLEANER(order.cleaner) } } });

  const session = {
    mode: 'payment',
    line_items,
    success_url: `${origin}/?checkout=success&session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: order.link || `${origin}/`,
    shipping_address_collection: { allowed_countries: PRICING.shipCountries },
    phone_number_collection: { enabled: true },
    metadata,
    // Every paid order gets a numbered Stripe invoice, emailed with a PDF and listed under Invoices.
    customer_creation: 'always',
    tax_id_collection: { enabled: true },
    invoice_creation: { enabled: true, invoice_data: {
      description: `GUTS rib seat cover for ${order.make} ${order.model} ${order.year}`.slice(0, 1500),
      metadata,
    } },
    payment_intent_data: { metadata, description: `Seat cover: ${order.make} ${order.model} ${order.year} ×${order.qty}` },
  };
  if (PRICING.shipping > 0) session.shipping_options = [{ shipping_rate_data: {
    type: 'fixed_amount', display_name: 'Shipping', fixed_amount: { amount: cents(PRICING.shipping), currency } } }];

  try {
    const r = await fetch('https://api.stripe.com/v1/checkout/sessions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: encode(session),
    });
    const data = await r.json();
    if (!r.ok) { console.error('Stripe error', data.error); return res.status(502).json({ error: 'Payment provider error, please try again.' }); }
    return res.status(200).json({ url: data.url });
  } catch (e) {
    console.error(e); return res.status(502).json({ error: 'Could not reach the payment provider.' });
  }
}

const OPTIONS_CLEANER = c => (c === 'towel' ? 'GUTS 2-in-1 Cleaner with towel' : 'GUTS 2-in-1 Cleaner');
