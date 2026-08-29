# The double-clickable app

`Job App.app` sits in the project root. Double-click it and the dashboard opens in your
browser. That is the whole interaction.

Three things it does that `run.command` does not:

- **No Terminal window.** It is a real macOS app bundle with its own icon and Dock entry
- **It waits for the server before opening the browser.** A tab that lands on a connection
  error and has to be reloaded is the entire difference between this and a shell script
- **Launching it twice does not start a second server.** If one is already listening it
  just brings the dashboard forward

Quit it from the Dock and the server stops with it.

## The one-time permission

The project lives under `~/Desktop`, which macOS protects. The first time you double-click
the app, macOS asks whether Job App may read your Desktop folder. **Say yes.** Without it
the app cannot read its own code and will not start.

If you miss the prompt, the app says so and offers to open the right Settings pane. You can
also get there yourself: System Settings, Privacy and Security, Files and Folders.

Moving the project somewhere outside `~/Desktop`, `~/Documents` or `~/Downloads` removes
this requirement entirely, because those three are the only folders macOS gates.

## Rebuilding it

```bash
python3 desktop/make_icon.py     # only if the icon changed
bash desktop/build_app.sh        # rebuilds and re-signs the bundle
```

`build_app.sh` ad-hoc signs the result. That is not about trust, it is about identity:
an unsigned bundle has no stable one, so macOS cannot remember the permission you granted
and every launch fails the same way.

The bundle itself is a build artefact and is gitignored. The three files it is built from
are not.

## When something goes wrong

The log is at `~/Library/Logs/JobApp.log`, deliberately outside the project, because the
project folder is the thing macOS may be refusing to let the app touch.
