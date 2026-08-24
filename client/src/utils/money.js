/** Money formatting for commercial figures.
 *
 * ONE implementation, deliberately. This logic previously existed twice — once in the org
 * Event Commercial tab and once in the super-admin commerce console. When the "USD" fallback
 * was removed from the first copy, the second kept it, and an admin viewing a GBP order saw
 * dollars. A single exported function is what stops that divergence recurring.
 *
 * There is NO default currency. ZST-LE-COM-001 forbids a universal currency fallback: a
 * missing currency is missing information, and rendering it as USD states something the data
 * never said. Callers pass the currency from the backend's own order/payment/invoice record.
 */

/**
 * @param {string|number|null|undefined} amount
 * @param {string|null|undefined} currency ISO 4217 code from the commercial record.
 * @returns {string} A formatted figure, never a guessed currency.
 */
export const money = (amount, currency) => {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  if (amount == null || Number.isNaN(n)) return "—";
  const code = (currency || "").trim().toUpperCase();
  if (!code) {
    // No currency on the record: show the number plainly. Deliberately no symbol — inventing
    // one would misrepresent the figure, and "1,440.00" is honest about what we know.
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2 }).format(n);
  }
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: code }).format(n);
  } catch {
    // Unrecognized code: show the number with the code beside it rather than guessing.
    return `${n.toFixed(2)} ${code}`;
  }
};

export default money;
