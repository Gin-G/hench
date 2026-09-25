<script>
  // Add or edit one hand-entered bill. Filling in a balance makes it a debt
  // too, listed alongside the Plaid cards.
  import { untrack } from "svelte";

  let { bill = null, onSave, onCancel } = $props();

  const FREQUENCIES = ["once", "weekly", "biweekly", "monthly", "quarterly", "annually"];

  // The fields seed from the bill once; the parent remounts the form per bill.
  const initial = untrack(() => bill);
  let name = $state(initial?.name ?? "");
  let amount = $state(initial?.amount ?? "");
  let frequency = $state(initial?.frequency ?? "monthly");
  let nextDue = $state(initial?.next_due_date ?? "");
  let autopay = $state(initial?.autopay ?? false);
  let isDebt = $state(initial?.balance != null);
  let balance = $state(initial?.balance ?? "");
  let apr = $state(initial?.apr != null ? String(Number(initial.apr)) : "");
  let notes = $state(initial?.notes ?? "");
  let saving = $state(false);
  let error = $state(null);

  // Blank money fields go to the API as null rather than "".
  const orNull = (v) => (v === "" || v == null ? null : String(v));

  async function submit(e) {
    e.preventDefault();
    saving = true;
    error = null;
    const body = {
      name: name.trim(),
      amount: orNull(amount),
      frequency,
      next_due_date: nextDue,
      autopay,
      balance: isDebt ? orNull(balance) : null,
      apr: isDebt ? orNull(apr) : null,
      notes: notes.trim() || null,
    };
    // When editing, send only what changed. Re-sending an unchanged due date
    // would re-anchor the day of month — a bill due on the 31st, currently
    // showing Nov 30, would come back on the 30th from then on.
    if (initial) {
      for (const [k, v] of Object.entries(body)) {
        const was = initial[k] == null ? null : String(initial[k]);
        const now = v == null ? null : String(v);
        if (was === now || (was !== null && now !== null && Number(was) === Number(now))) {
          delete body[k];
        }
      }
    }
    try {
      await onSave(body);
    } catch (err) {
      error = String(err);
    } finally {
      saving = false;
    }
  }
</script>

<form onsubmit={submit}>
  <div class="grid">
    <label class="wide">
      Name
      <input bind:value={name} required placeholder="Rent, Electric, Car loan…" />
    </label>
    <label>
      Amount
      <input type="number" step="0.01" min="0" bind:value={amount} placeholder="varies" />
    </label>
    <label>
      Repeats
      <select bind:value={frequency}>
        {#each FREQUENCIES as f}<option value={f}>{f}</option>{/each}
      </select>
    </label>
    <label>
      Next due
      <input type="date" bind:value={nextDue} required />
    </label>
    <label class="check">
      <input type="checkbox" bind:checked={autopay} /> Autopay
    </label>
    <label class="check">
      <input type="checkbox" bind:checked={isDebt} /> Has a balance owed
    </label>
    {#if isDebt}
      <label>
        Balance owed
        <input type="number" step="0.01" min="0" bind:value={balance} required />
      </label>
      <label>
        APR %
        <input type="number" step="0.01" min="0" max="100" bind:value={apr} />
      </label>
    {/if}
    <label class="wide">
      Notes
      <input bind:value={notes} placeholder="optional" />
    </label>
  </div>
  {#if error}<p class="err">{error}</p>{/if}
  <div class="actions">
    <button type="button" onclick={onCancel}>Cancel</button>
    <button type="submit" class="primary" disabled={saving}>
      {saving ? "Saving…" : initial ? "Save" : "Add bill"}
    </button>
  </div>
</form>

<style>
  form {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
    margin-bottom: 1rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: 0.75rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    font-size: 0.82rem;
    color: var(--muted);
  }
  label.wide {
    grid-column: span 2;
  }
  label.check {
    flex-direction: row;
    align-items: center;
    gap: 0.45rem;
    color: var(--text);
    font-size: 0.9rem;
  }
  input:not([type="checkbox"]),
  select {
    background: var(--panel);
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.9rem;
  }
  .err {
    color: var(--danger);
    font-size: 0.85rem;
  }
  @media (max-width: 480px) {
    label.wide {
      grid-column: auto;
    }
  }
</style>
