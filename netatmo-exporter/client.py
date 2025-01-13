#!/usr/bin/env python3
# encoding=utf-8

import argparse
import logging
import signal
from enum import Enum
from os import getenv
from pathlib import Path
from time import sleep
from typing import Tuple, Optional, Dict

import pyatmo.helpers
import yaml
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError
from prometheus_client import start_http_server, Gauge
from pyatmo import NetatmoOAuth2, WeatherStationData, ApiError
from requests import ConnectionError


class TrendState(Enum):
    UP = 1
    DOWN = -1
    STABLE = 0


def parse_config(_config_file=None) -> Tuple[Dict, str]:
    if _config_file is None:
        _config_file = Path("config.yaml")

    try:
        with open(_config_file, "r") as _file:
            _config = yaml.safe_load(_file)
    except FileNotFoundError:
        log.error("Config file does not exist.")
    except yaml.YAMLError as _error:
        if hasattr(_error, "problem_mark"):
            _mark = _error.problem_mark
            log.error("Error in configuration")
            log.error(f"Error position: ({_mark.line + 1}:{_mark.column + 1})")
    else:
        return _config, _config_file


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--file", dest="config_file", type=str, nargs=1, required=False)
    parser.add_argument("-v", "--verbose", dest="verbosity", action="count", default=0)

    return parser.parse_args()


def set_logging_level(_verbosity, _level, _logger=None):
    _switcher = {
        1: "WARNING",
        2: "INFO",
        3: "DEBUG",
    }
    if _verbosity > 0:
        _level = _switcher.get(_verbosity)

    _fmt = logging.Formatter(
        "%(asctime)s - %(module)s:%(lineno)d - %(levelname)s:%(message)s", datefmt="%d.%m.%Y %H:%M:%S"
    )

    # Basic Setting for Debugging
    pyatmo.helpers.LOG.setLevel(_level)

    # Logger
    if _logger is None:
        _logger = logging.getLogger(__name__)

    _ch = logging.StreamHandler()
    _ch.setFormatter(_fmt)

    _logger.addHandler(_ch)
    _logger.setLevel(_level)
    _logger.info(f"Setting loglevel to {_level}.")

    return _logger


def get_authorization(
    _client_id: str, _client_secret: str, _refresh_token: str, _token_expiration: float = 0
) -> Tuple[NetatmoOAuth2, str, float]:
    while True:
        try:
            _auth = NetatmoOAuth2(
                client_id=_client_id,
                client_secret=_client_secret,
            )
            _auth.extra["refresh_token"] = _refresh_token
            _result = _auth.refresh_tokens()
            _refresh_token = _result.get("refresh_token")

            override = {"netatmo": {"refresh_token": _refresh_token}}
            with open(config_file, "w") as f:
                if "netatmo" in config:
                    config["netatmo"]["refresh_token"] = _refresh_token
                    f.write(yaml.dump(config))
                else:
                    f.write(yaml.dump(override))
                log.info(f"Refresh Token updated. New Token is: {_refresh_token}")

            return _auth, _refresh_token, _token_expiration
        except ApiError:
            log.error("No credentials supplied. No Netatmo Account available.")
            exit(1)
        except ConnectionError:
            log.error(f"Can't connect to Netatmo API. Retrying in {interval} second(s)...")
            pass
        except InvalidGrantError:
            log.error("Refresh Token expired!")
            exit(1)


def safe_list_get(_input_list: list, _idx: int, _default=None) -> Optional[str | int | float]:
    try:
        return _input_list[_idx]
    except IndexError:
        return _default


def get_sensor_data(_sensor_data: dict, _station_name: str, _module_name: str, _module_type: str) -> None:
    if _sensor_data is not None:
        for _sensor, _value in _sensor_data.items():
            if _sensor in ["time_utc", "date_max_temp", "date_min_temp", "date_max_wind_str"]:
                continue
            if _sensor in ["temp_trend", "pressure_trend"]:
                globals()[_sensor.upper()].labels(_station_name, _module_name, _module_type).set(
                    TrendState[_value.upper()].value
                )
                continue
            globals()[_sensor.upper()].labels(_station_name, _module_name, _module_type).set(_value)


def shutdown(_signal):
    global running
    running = False


if __name__ == "__main__":
    running = True
    client_id = None
    client_secret = None
    refresh_token = None
    token_expiration = 0
    args = parse_args()
    config, config_file = parse_config(args.config_file)

    if getenv("TERM", None):
        # noinspection PyTypeChecker
        signal.signal(signal.SIGTERM, shutdown)
        # noinspection PyTypeChecker
        signal.signal(signal.SIGINT, shutdown)

    interval = int(config.get("interval", "300"))  # interval in seconds; default are 5 Minutes
    loglevel = config.get("loglevel", "INFO")  # set loglevel by Name
    listen_port = int(config.get("listen_port", "9126"))  # set loglevel for batching (influx)

    if "netatmo" in config:
        client_id = config.get("netatmo").get("client_id", None)
        client_secret = config.get("netatmo").get("client_secret", None)
        refresh_token = config.get("netatmo").get("refresh_token", None)

    # Environment Variables takes precedence over config if set
    # global
    interval = int(getenv("INTERVAL", interval))
    loglevel = getenv("LOGLEVEL", loglevel)
    listen_port = getenv("LISTEN_PORT", listen_port)
    # netatmo
    client_id = getenv("NETATMO_CLIENT_ID", client_id)
    client_secret = getenv("NETATMO_CLIENT_SECRET", client_secret)
    # refresh_token needs to be persisted in the config, but can be set as env var for first run
    refresh_token = getenv("NETATMO_REFRESH_TOKEN", refresh_token)

    # set logging level
    log = set_logging_level(args.verbosity, loglevel)

    # Prometheus Metrics
    STATION_REACHABLE = Gauge(
        "netatmo_station_reachable", "If the station is reachable", ["station", "type", "city", "country", "timezone"]
    )
    STATION_LONGITUDE = Gauge("netatmo_station_longitude", "The Longitude of the Station", ["station", "type"])
    STATION_LATITUDE = Gauge("netatmo_station_latitude", "The Latitude of the Station", ["station", "type"])
    STATION_ALTITUDE = Gauge("netatmo_station_altitude", "The Altitude of the Station", ["station", "type"])
    STATION_WIFI_STATUS = Gauge("netatmo_station_wifi_status", "The current Wifi Status", ["station", "type"])
    STATION_CO2_CALIBRATING = Gauge(
        "netatmo_station_co2_calibrating", "The current CO2 Calibrating Status", ["station", "type"]
    )
    TEMPERATURE = Gauge("netatmo_temperature", "The current Temperature", ["station", "module", "type"])
    MIN_TEMP = Gauge("netatmo_temperature_min", "The current Min Temperature", ["station", "module", "type"])
    MAX_TEMP = Gauge("netatmo_temperature_max", "The current Max Temperature", ["station", "module", "type"])
    TEMP_TREND = Gauge("netatmo_temperature_trend", "The current Temperature Trend", ["station", "module", "type"])
    HUMIDITY = Gauge("netatmo_humidity", "The current Humidity", ["station", "module", "type"])
    CO2 = Gauge("netatmo_co2", "The current CO2", ["station", "module", "type"])
    PRESSURE = Gauge("netatmo_pressure", "The current Pressure", ["station", "module", "type"])
    PRESSURE_TREND = Gauge("netatmo_pressure_trend", "The current Pressure Trend", ["station", "module", "type"])
    ABSOLUTEPRESSURE = Gauge(
        "netatmo_absolute_pressure", "The current Absolute Pressure", ["station", "module", "type"]
    )
    NOISE = Gauge("netatmo_noise", "The current Noise", ["station", "module", "type"])
    RF_STATUS = Gauge("netatmo_rf_status", "The current RF Status", ["station", "module", "type"])
    BATTERY_VP = Gauge("netatmo_battery_vp", "The current Battery VP", ["station", "module", "type"])
    BATTERY_PERCENT = Gauge("netatmo_battery_percent", "The current Battery Percent", ["station", "module", "type"])
    WINDANGLE = Gauge("netatmo_wind_angle", "The current Wind Angle", ["station", "module", "type"])
    WINDSTRENGTH = Gauge("netatmo_wind_strength", "The current Wind Strength", ["station", "module", "type"])
    MAX_WIND_ANGLE = Gauge("netatmo_wind_max_angle", "The current Wind Max Angle", ["station", "module", "type"])
    MAX_WIND_STR = Gauge("netatmo_wind_max_strength", "The current Wind Max Strength", ["station", "module", "type"])
    GUSTANGLE = Gauge("netatmo_gust_angle", "The current Gust Angle", ["station", "module", "type"])
    GUSTSTRENGTH = Gauge("netatmo_gust_strength", "The current Gust Strength", ["station", "module", "type"])
    RAIN = Gauge("netatmo_rain", "The current Rain", ["station", "module", "type"])
    SUM_RAIN_1 = Gauge("netatmo_rain_1h", "Rain over the last 1h", ["station", "module", "type"])
    SUM_RAIN_24 = Gauge("netatmo_rain_24h", "Rain over the last 24h", ["station", "module", "type"])

    start_http_server(listen_port)
    log.info("Exporter ready...")
    while running:
        authorization, refresh_token, token_expiration = get_authorization(
            client_id, client_secret, refresh_token, token_expiration
        )
        try:
            weatherData = WeatherStationData(authorization)
            weatherData.update()

            for station in weatherData.stations.values():
                log.debug(f"Station Data: {station}")
                station_name = station.get("home_name", "Unknown")
                station_module_name = station.get("module_name", "Unknown")
                station_module_type = station.get("type", "Unknown")
                station_place = station.get("place", {})
                station_country = station_place.get("country", "Unknown")
                station_timezone = station_place.get("timezone", "Unknown")
                station_city = station_place.get("city", "Unknown")
                station_long_lat = station_place.get("location", [])

                station_data = {
                    "altitude": station_place.get("altitude"),
                    "longitude": safe_list_get(station_long_lat, 0),
                    "latitude": safe_list_get(station_long_lat, 1),
                }

                for key, value in station_data.items():
                    globals()[f"STATION_{key.upper()}"].labels(station_name, station_module_type).set(value)

                station_sensor_data = station.get("dashboard_data")

                if station_sensor_data is None:
                    continue

                STATION_REACHABLE.labels(
                    station_name, station_module_type, station_city, station_country, station_timezone
                ).set(station.get("reachable"))

                for sensor in ["wifi_status", "co2_calibrating"]:
                    globals()[f"STATION_{sensor.upper()}"].labels(station_name, station_module_type).set(
                        station.get(sensor)
                    )

                get_sensor_data(station_sensor_data, station_name, station_module_name, station_module_type)

                for module in station.get("modules"):
                    log.debug(f"Module Data: {module}")
                    module_name = module.get("module_name")
                    module_type = module.get("type")

                    module_sensor_data = module.get("dashboard_data")

                    if module_sensor_data is None:
                        continue

                    for sensor in ["rf_status", "battery_vp", "battery_percent"]:
                        globals()[f"{sensor.upper()}"].labels(station_name, module_name, module_type).set(
                            module.get(sensor)
                        )

                    get_sensor_data(module_sensor_data, station_name, module_name, module_type)
        except ApiError as error:
            log.error(error)
            pass
        finally:
            sleep(interval)
