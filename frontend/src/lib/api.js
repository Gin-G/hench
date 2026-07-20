// All calls go through /api; Traefik (prod) / Vite proxy (dev) strip the
// prefix so the FastAPI backend serves these routes at root.
const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // Link
  createLinkToken: (itemId = null) =>
    req("/link/create_link_token", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId }),
    }),
  exchangePublicToken: (publicToken) =>
    req("/link/exchange", {
      method: "POST",
      body: JSON.stringify({ public_token: publicToken }),
    }),
  items: () => req("/items"),
  syncNow: (itemId = null) =>
    req(`/sync${itemId ? `?item_id=${encodeURIComponent(itemId)}` : ""}`, {
      method: "POST",
    }),

  // Sankey
  sankey: (month) => req(`/sankey${month ? `?month=${month}` : ""}`),
  months: () => req("/sankey/months"),
  categories: () => req("/categories"),

  // Transactions
  transactions: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== "" && v !== undefined)
    );
    return req(`/transactions${q.toString() ? `?${q}` : ""}`);
  },
  setCategory: (txnId, categoryPrimary, categoryDetailed = null) =>
    req(`/transactions/${encodeURIComponent(txnId)}/category`, {
      method: "PUT",
      body: JSON.stringify({
        category_primary: categoryPrimary,
        category_detailed: categoryDetailed,
      }),
    }),
  clearCategory: (txnId) =>
    req(`/transactions/${encodeURIComponent(txnId)}/category`, {
      method: "DELETE",
    }),
};

// Human-friendly label for Plaid's SCREAMING_SNAKE_CASE categories.
export function prettyCategory(c) {
  if (!c) return "Uncategorized";
  return c
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function fmtMoney(n) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(n ?? 0);
}
