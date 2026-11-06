# GATBOX

GATBOX is the bench test rig, a Raspberry Pi that records power-rail traces. It posts them
to the app.

1. Add the rig under [Settings](Settings) → Devices and give it the token, which is shown
   once. Under the device, **Acts as and jobs**: pick the person its changes are made as
   (the audit log shows "that person via the rig"), and tick **Jobs** so it can see each
   machine's open jobs and raise orders from the bench. Without Jobs it still sends traces.
2. Traces from a machine appear on that machine and can be put on its open job.
3. The job page compares as-found against as-left on one chart, and the service report
   prints the same comparison.

The rig and the app talk through a small versioned API (contract v1). Its spec is in the
repository under
[`contract/v1/`](https://github.com/digitalunconciousness/ArcadeTech-Tracker/tree/main/contract/v1).

## The game library

Once the archive is set up (the owner does this; see the RUNBOOK), an asset that names a game
(its **Manuals key**, like `widget-wars`) shows **Manual and limits** on its page: the game's
documents, which open in the browser behind your sign-in, the rail limits written down from the
manual, and a few facts such as fuse values. It is a read-only copy, refreshed every ten minutes,
and the page says when.

- A limit marked **unconfirmed** was written down from a manual and nobody has checked it against
  the page yet. Don't judge a board by it.
- Owners and techs get **Edit it in the library**, which opens the game at the archive. Changes
  are made there, never here.
- **The archive has no game with that key** means the key was read from the archive and nothing there
  has it: a typo, or a game nobody has added there yet (the deck sends the barcade's roster, so a
  machine that isn't on it needs its game added at the archive first). Owners and techs get
  **Find it at the archive** and **Add it at the archive**. Add the game there and use the key it
  shows, or fix the key here. The next pull, within ten minutes, picks it up and the message goes.
- Without the archive set up, the section isn't there.
