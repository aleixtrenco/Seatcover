/* Shop rules shared by the page (index.html) and the Stripe checkout function (api/checkout.js).
   The server recomputes every price from these rules, so the customer's browser can't change what they pay. */

export const PRICING = {
  currency: 'AED',
  locale: 'en-AE',
  basePrice: 294,
  prices: { ballistic: 62, bump: 117, step: 117, cleaner: 48, cleanerTowel: 81 },
  shipping: 0,                                          // flat shipping per order (0 = free)
  shipCountries: ['AE', 'SA', 'OM', 'QA', 'BH', 'KW'],  // countries customers can ship to (ISO codes)
};

export const COLOR_NAMES = {
  white: 'White', grey: 'Grey', red: 'Red', orange: 'Orange', yellow: 'Yellow', floyellow: 'Flo Yellow',
  green: 'Green', teal: 'Teal', yzblue: 'YZ / Beta Blue', huskyblue: 'KTM / Husky Blue', navy: 'Navy',
  pink: 'Pink', purple: 'Purple', brown: 'Brown', camo: 'Camo', black: 'Black',
};

export const OPTIONS = {
  height: { tall13: '+13mm Taller than Stock', tall30: '+30mm Taller than Stock', stock: 'Stock Height', low20: '-20mm Lower than Stock' },
  material: { original: 'Original grip (aggressive)', soft: 'Less aggressive' },
  logo: { full: 'GUTS top rear + GUTS side logos', top: 'GUTS top rear only, no side logos' },
  attach: { staple: 'Staple on', velcro: 'Velcro System (VS patch)' },
  bump: { none: 'None', bump: 'Bump', step05: '1/2" thick step', step1: '1" thick step' },
  cleaner: { none: 'None', cleaner: '2-in-1 Cleaner', towel: '2-in-1 Cleaner with towel' },
};

const text = (v, max) => {
  if (typeof v !== 'string' || !v.trim() || v.length > max || !/^[\p{L}\p{N} .,'/()+\-]+$/u.test(v)) throw new Error('Invalid bike details');
  return v.trim();
};
const pick = (v, allowed, what) => { if (!Object.hasOwn(allowed, v)) throw new Error(`Please choose ${what}`); return v; };

// Returns a clean order object or throws with a customer-friendly message.
export function validate(o) {
  if (!o || typeof o !== 'object') throw new Error('Invalid order');
  const colors = {};
  for (const part of ['side', 'center', 'rib']) colors[part] = pick(o.colors?.[part], COLOR_NAMES, `a ${part} color`);
  const order = {
    make: text(o.make, 30), model: text(o.model, 30), year: text(String(o.year ?? ''), 4),
    colors,
    height: pick(o.height, OPTIONS.height, 'a seat height'),
    material: pick(o.material, OPTIONS.material, 'a material'),
    logo: pick(o.logo, OPTIONS.logo, 'a logo placement'),
    attach: pick(o.attach, OPTIONS.attach, 'staple-on or Velcro'),
    bump: pick(o.bump ?? 'none', OPTIONS.bump, 'a bump or step option'),
    cleaner: pick(o.cleaner ?? 'none', OPTIONS.cleaner, 'a cleaner option'),
    noRibs: o.noRibs === true,
    ballistic: o.ballistic === true,
    qty: Number.isInteger(o.qty) && o.qty >= 1 && o.qty <= 20 ? o.qty : (() => { throw new Error('Quantity must be 1–20'); })(),
    link: typeof o.link === 'string' && o.link.length <= 480 && /^https?:\/\//.test(o.link) ? o.link : '',
  };
  if (order.attach === 'velcro' && order.bump !== 'none') throw new Error('A bump or step is not available with the Velcro System');
  return order;
}

// Prices per seat cover and per cleaner (both multiplied by qty).
export function quote(o) {
  const P = PRICING.prices;
  let seat = PRICING.basePrice;
  if (o.ballistic) seat += P.ballistic;
  if (o.bump === 'bump') seat += P.bump; else if (o.bump && o.bump !== 'none') seat += P.step;
  const cleaner = o.cleaner === 'cleaner' ? P.cleaner : o.cleaner === 'towel' ? P.cleanerTowel : 0;
  const unit = seat + cleaner;
  return { seat, cleaner, unit, total: unit * o.qty + (PRICING.shipping || 0) };
}

// Human-readable spec: [label, value] rows (used on the page and as Stripe order metadata).
export function describe(o) {
  const c = p => COLOR_NAMES[o.colors[p]];
  return [
    ['Bike', `${o.make} ${o.model} ${o.year}`],
    ['Height', OPTIONS.height[o.height]],
    ['Material', OPTIONS.material[o.material]],
    ['Colors', `Side ${c('side')} / Center ${c('center')} / ${o.noRibs ? 'No ribs (2 colors)' : `Ribs ${c('rib')}`}`],
    ['Logos', OPTIONS.logo[o.logo]],
    ['Attachment', OPTIONS.attach[o.attach]],
    ['Ballistic nylon knee', o.ballistic ? 'Yes (black)' : 'No'],
    ['Bump / step', OPTIONS.bump[o.bump]],
    ['Cleaner', OPTIONS.cleaner[o.cleaner]],
    ['Quantity', String(o.qty)],
  ];
}
