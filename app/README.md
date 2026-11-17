# Your Fabric App goes here

Put the Rayfin project of your Fabric App in this folder, so that
`app/package.json` and `app/rayfin/rayfin.yml` exist. Until then, the CI
and deploy pipelines skip their work and pass.

To start a new app, scaffold it next to the repository and move its content
here, its own `.gitignore` included:

```bash
npm create @microsoft/rayfin@latest -- my-app --template blankapp
```

Or copy an existing Rayfin project into this folder. Then replace this file
with the app's own README.

What the pipelines expect from the app is in the root README, under
**Put your app in `app/`**.
