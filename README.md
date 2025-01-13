# netatmo-exporter
![pyatmo](./pyatmo.png)

Netatmo Weather Station dashboard for Grafana based on Prometheus


## Installation
* Create a [Netatmo developer account](https://dev.netatmo.com/apidocumentation) and create an app there.
* Generate a refresh token in your app, scroll down to the "Token generator" and generate a new one with the appropriate scopes.
* Create file called "config" or use Environment Variables and fill in your NETATMO_CLIENT_ID and NETATMO_CLIENT_SECRET.
  * Because of recent changes `refresh_token` needs to be added to a configfile as it needs to be re-generated during runtime and will be written to the config.
* Environment Variables take precedence over everything else and will overwrite your config vars.
* The default is to search for a config file right next to the script, but you can point to any config file with the "-f" switch.

```yaml
interval: 600
loglevel: INFO
listen_port: 9126

netatmo:
  client_id: ""
  client_secret: ""
  refresh_token: ""
```

```
NETATMO_CLIENT_ID=
NETATMO_CLIENT_SECRET=
LISTEN_PORT=9126
INTERVAL=600
LOGLEVEL=INFO
```


## Prometheus Scraper
```yaml
---
scrape_configs:
  - job_name: netatmo_exporter
    static_configs:
      - targets: ['netatmo-exporter:9126']
```
