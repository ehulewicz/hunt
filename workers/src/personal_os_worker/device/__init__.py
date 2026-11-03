"""The device agent: this machine as a Hunt worker the Vercel page can see and drive.

`identity` is who this device is, `update` keeps its checkout current, `heartbeat`
lists it as up and ready, `server` is the agent the page connects to over the
tailnet, `stream` is the live Chrome tab over `cdp` with `pane` mapping its
input, `runs` the apply runs it starts or mirrors, and `link` the socket an
apply run reports to it through.
"""
