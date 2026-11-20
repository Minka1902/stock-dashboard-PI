# Moved: this repository is retired

The Raspberry Pi version of the Stock Signal Dashboard now lives in the main
repository, **[Minka1902/stock-dashboard](https://github.com/Minka1902/stock-dashboard)**,
together with the Windows and Linux versions. One tool installs, updates, backs
up and restores it on every machine (`signal install`, `signal update`,
`signal backup`, `signal restore`). See its
[`deploy/README.md`](https://github.com/Minka1902/stock-dashboard/blob/main/deploy/README.md).

Nothing new lands here, and the in-app updater of a Pi installed from this
repository will not see further updates.

## Moving a Pi installed from this repository

On the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/Minka1902/stock-dashboard/main/install.sh | sudo bash
```

It recognises the old install in `/opt/stocks-dashboard`, takes a verified backup
first, refuses if the code there was edited by hand, points the checkout at the
main repository and reinstalls the services. Your database, backups and
`/etc/stocks-dashboard/stocks.env` (admin email, keys) are kept as they are.

Afterwards `sudo signal status` shows the service, its health and the last backup.
