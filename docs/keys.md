# License keys: current state and the rotation intent

This note is for me and for anyone auditing the project. It is not a customer-facing document.

## What ships today

Every buyer sees the same string on `docs/success.html`:

    claimaudit-early-2026

That is the one shared early-access key. `claimaudit/license.py` accepts any non-empty
stripped token, so the specific string is not the gate — the honor system is. The
customer-visible framing on `docs/success.html`, `docs/index.html` and `README.md` is
consistent about this, and calls it out in the same words in all three places:
"anyone who has the address of this page can see the key", "any non-empty key
unlocks the paid checks", "anyone who reads the code can bypass it". The MIT license
grants the code regardless of whether a key is present.

## Why it is worth writing this down now

At the time the shared key was chosen, the number of buyers was small enough that the
tradeoff (buyers get the tool the instant they pay, no per-key issuance step) clearly
outweighed the leak surface. That number is no longer small: real purchases are into
the hundreds. The tradeoff itself was a deliberate, already-approved decision and this
note is not overturning it. But the framing on `docs/success.html` still says "one
shared early-access key" — which is accurate today and reads as a temporary state.
Nothing in the repo currently promises that it will end, and nobody has written down
what would prompt the change.

## The intent, plainly

Rotate to per-buyer keys before the customer count or the value of a paid check makes
the shared-key story feel like a broken promise rather than an honest tradeoff. The
switch is not urgent as of this note (the audit found no submission blockers over
it), but the decision to do it eventually is on record here so a later reader is not
guessing.

Concretely, when the switch happens it needs:

* A stable place to issue per-buyer keys from — Stripe webhook to a small script,
  or a manual issuance flow, either is fine. No license server, still no phone-home.
* A migration for existing buyers: the shared key must keep working for them, or
  they must be given the new key in a way that does not require re-buying.
* One line in `docs/success.html` for new buyers to say what changed.
* Nothing in `claimaudit/license.py` has to change: the file already accepts any
  non-empty stripped key. Per-buyer keys are the same code path.

## What would not be a reason to rush this

* The shared key being publicly guessable. It already is: it is printed in plain
  text on a page that anyone with the URL can load, and the code does not check it
  against anything.
* Somebody using the tool without paying. The MIT license grants that, and the
  honor system depends on people who benefit from the tool wanting to support it.
