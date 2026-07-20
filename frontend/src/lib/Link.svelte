<script>
  import { api } from "./api.js";

  // itemId set => Link "update mode" (re-auth an existing Item).
  let { itemId = null, label = "Connect a bank", onDone = () => {} } = $props();

  let busy = $state(false);
  let error = $state(null);

  async function open() {
    busy = true;
    error = null;
    try {
      const { link_token } = await api.createLinkToken(itemId);
      const handler = window.Plaid.create({
        token: link_token,
        onSuccess: async (public_token) => {
          try {
            if (itemId) {
              // Update mode: token unchanged, just resync.
              await api.syncNow(itemId);
            } else {
              await api.exchangePublicToken(public_token);
            }
            onDone();
          } catch (e) {
            error = String(e);
          } finally {
            busy = false;
          }
        },
        onExit: (err) => {
          busy = false;
          if (err) error = err.error_message || err.display_message || "Link exited";
        },
      });
      handler.open();
    } catch (e) {
      error = String(e);
      busy = false;
    }
  }
</script>

<button class:primary={!itemId} onclick={open} disabled={busy}>
  {busy ? "Opening…" : label}
</button>
{#if error}
  <span style="color: var(--danger); margin-left: 0.5rem;">{error}</span>
{/if}
