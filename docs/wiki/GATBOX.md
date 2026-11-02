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
