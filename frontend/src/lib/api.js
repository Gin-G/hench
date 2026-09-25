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

  // Bills and debt
  bills: () => req("/bills"),
  createBill: (bill) => req("/bills", { method: "POST", body: JSON.stringify(bill) }),
  updateBill: (id, changes) =>
    req(`/bills/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
  deleteBill: (id) => req(`/bills/${id}`, { method: "DELETE" }),
  markBillPaid: (id) => req(`/bills/${id}/paid`, { method: "POST" }),
  upcoming: (days = 30) => req(`/upcoming?days=${days}`),
  debts: () => req("/debts"),
  renameAccount: (accountId, nickname) =>
    req(`/accounts/${encodeURIComponent(accountId)}`, {
      method: "PATCH",
      body: JSON.stringify({ nickname }),
    }),
  hiddenStreams: () =>
    req("/recurring?direction=outflow&include_hidden=true").then((streams) =>
      streams.filter((s) => s.hidden)
    ),
  setStreamHidden: (streamId, hidden) =>
    req(`/recurring/${encodeURIComponent(streamId)}`, {
      method: "PATCH",
      body: JSON.stringify({ hidden }),
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

// Accepts numbers or the decimal strings the bills/debt endpoints return.
export function fmtMoney(n) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(n ?? 0));
}
