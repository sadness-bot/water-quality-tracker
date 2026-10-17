from machine import ADC, Pin, UART
import time
import network
import espnow
import urequests
import gc

PH7_ADC = 1788
PH4_ADC = 2432

READ_INTERVAL_MS = 2000
SMS_COOLDOWN_MS = 1200000
REMOTE_OVERRIDE_MS = 300000

SMS_PHONE = "+15551234567"

WIFI_SSID = "Sargassum-Dashboard"
WIFI_PASSWORD = "Sargassum2026"
SERVER_URL = "http://10.42.0.1:5000/api/data"
STATION_ID = "coastline-01"

CAL1_V_MV = 1981.2
CAL1_T_C = 22
WATER_TEMP_C = 22

DO_TABLE = [
    14460, 14220, 13820, 13440, 13090, 12740, 12420,
    12110, 11810, 11530, 11260, 11010, 10770, 10530,
    10300, 10080, 9860, 9660, 9460, 9270, 9080,
    8900, 8730, 8570, 8410, 8250, 8110, 7960,
    7820, 7690, 7560, 7430, 7300, 7180, 7070,
    6950, 6840, 6730, 6630, 6530, 6410
]

# Simulated values used during remote demonstrations.
SIMULATED_READINGS = {
    "Normal": {
        "ph": 7.2,
        "do": 7.5,
        "h2s": 0.2
    },
    "Warning": {
        "ph": 6.3,
        "do": 4.2,
        "h2s": 2.0
    },
    "Danger": {
        "ph": 5.0,
        "do": 1.0,
        "h2s": 8.0
    }
}

sms_cooldown_warning = 0
sms_cooldown_danger = 0
sms_failure_backoff = 0
sms_failure_count = 0
clock_synced = False

remote_override_status = None
remote_override_until = 0

REMOTE_COMMANDS = {
    b"COASTLINE:NORMAL": "Normal",
    b"COASTLINE:WARNING": "Warning",
    b"COASTLINE:DANGER": "Danger"
}


def setup_adc(pin):
    sensor = ADC(Pin(pin))
    sensor.atten(ADC.ATTN_11DB)
    sensor.width(ADC.WIDTH_12BIT)
    return sensor


ph_sensor = setup_adc(34)
do_sensor = setup_adc(35)
h2s_sensor = setup_adc(32)

red = Pin(13, Pin.OUT)
green = Pin(12, Pin.OUT)
blue = Pin(14, Pin.OUT)


def median5(read_function):
    values = []

    for _ in range(5):
        values.append(read_function())
        time.sleep_ms(20)

    values.sort()
    return values[2]


def read_ph():
    raw = median5(ph_sensor.read)

    ph = 7.0 - (
        (raw - PH7_ADC) / (PH4_ADC - PH7_ADC)
    ) * 3.0

    return raw, round(max(0.0, min(14.0, ph)), 2)


def calculate_do(voltage_mv, temperature_c=WATER_TEMP_C):
    temperature_c = int(max(0, min(40, temperature_c)))

    saturation_voltage = (
        CAL1_V_MV + 35 * (temperature_c - CAL1_T_C)
    )

    if saturation_voltage <= 0:
        return 0.0

    do_ug_l = (
        voltage_mv * DO_TABLE[temperature_c]
        / saturation_voltage
    )

    return round(do_ug_l / 1000.0, 2)


def read_do():
    raw = median5(do_sensor.read)
    voltage_uv = median5(do_sensor.read_uv)
    voltage_mv = voltage_uv / 1000.0

    return raw, calculate_do(voltage_mv)


def read_h2s():
    raw = median5(h2s_sensor.read)

    # Temporary demonstration scale. This is not calibrated ppm.
    estimated_ppm = raw / 4095.0 * 20.0

    return raw, round(estimated_ppm, 2)


def get_ph_status(ph):
    if ph < 5.5 or ph > 9.0:
        return "Danger"

    if ph < 6.5 or ph > 7.75:
        return "Warning"

    return "Normal"


def get_do_status(do_value):
    if do_value < 2.0:
        return "Danger"

    if do_value < 5.0:
        return "Warning"

    return "Normal"


def get_h2s_status(h2s_value):
    if h2s_value > 5.0:
        return "Danger"

    if h2s_value > 1.0:
        return "Warning"

    return "Normal"


def get_overall_status(ph_status, do_status, h2s_status):
    statuses = (ph_status, do_status, h2s_status)

    if all(status == "Danger" for status in statuses):
        return "Danger"

    if any(status != "Normal" for status in statuses):
        return "Warning"

    return "Normal"


def get_effective_status(sensor_status):
    if remote_override_status is not None:
        return remote_override_status

    return sensor_status


def setup_espnow():
    radio = espnow.ESPNow()
    radio.active(True)
    print("ESP-NOW receiver ready")
    return radio


def process_remote_commands(radio):
    global remote_override_status, remote_override_until

    while True:
        try:
            packet = radio.recv(0)

            if packet is None:
                break

            sender, message = packet

            if message is None:
                break

            print("Remote command received:", message)

            if message == b"COASTLINE:CANCEL":
                remote_override_status = None
                remote_override_until = 0
                print("Override cancelled; returning to live sensors")

            elif message in REMOTE_COMMANDS:
                remote_override_status = REMOTE_COMMANDS[message]

                remote_override_until = time.ticks_add(
                    time.ticks_ms(), REMOTE_OVERRIDE_MS
                )

                print(
                    "Simulation mode:",
                    remote_override_status,
                    "for 5 minutes"
                )

        except Exception as error:
            print("ESP-NOW receive error:", error)
            break

    if (
        remote_override_status is not None
        and time.ticks_diff(
            time.ticks_ms(), remote_override_until
        ) >= 0
    ):
        print("Simulation ended; returning to live sensors")
        remote_override_status = None
        remote_override_until = 0


class AlertSystem:
    def __init__(self):
        self.status = None
        self.last_flash = time.ticks_ms()
        self.led_on = False

    def all_off(self):
        red.off()
        green.off()
        blue.off()

    def update(self, status):
        now = time.ticks_ms()

        if status != self.status:
            self.status = status
            self.last_flash = now
            self.led_on = False
            self.all_off()

        if status == "Normal":
            self.all_off()
            green.on()

        elif status == "Warning":
            self.all_off()
            red.on()
            green.on()

        elif status == "Danger":
            green.off()
            blue.off()

            if time.ticks_diff(now, self.last_flash) >= 500:
                self.last_flash = now
                self.led_on = not self.led_on

                if self.led_on:
                    red.on()
                else:
                    red.off()


alert_system = AlertSystem()


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)

    try:
        if not wlan.active():
            wlan.active(True)

        try:
            wlan.config(pm=wlan.PM_NONE)
        except Exception:
            pass

        if not wlan.isconnected():
            print("Connecting to Wi-Fi...")
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)

            start = time.ticks_ms()

            while not wlan.isconnected():
                if time.ticks_diff(
                    time.ticks_ms(), start
                ) > 15000:
                    print("Wi-Fi connection timed out")
                    return wlan

                time.sleep_ms(250)

        print("Wi-Fi connected:", wlan.ifconfig()[0])

    except Exception as error:
        print("Wi-Fi error:", error)

    return wlan


def sync_clock():
    global clock_synced

    try:
        import ntptime
        ntptime.settime()
        clock_synced = True
        print("Clock synchronized")

    except Exception as error:
        clock_synced = False
        print("Clock sync failed:", error)


class SmsSystem:
    def __init__(self):
        self.gsm = UART(
            2, baudrate=9600, tx=17, rx=16, timeout=1000
        )
        self.danger_count = 0
        self.warning_count = 0

    def clear_input(self):
        while self.gsm.any():
            self.gsm.read()

    def read_until(self, required, timeout=5000):
        start = time.ticks_ms()
        buffer = b""

        while time.ticks_diff(time.ticks_ms(), start) < timeout:
            if self.gsm.any():
                chunk = self.gsm.read()

                if chunk:
                    buffer += chunk

                    if (
                        b"ERROR" in buffer
                        or b"+CMS ERROR" in buffer
                    ):
                        return buffer

                    if all(pattern in buffer for pattern in required):
                        return buffer

            time.sleep_ms(25)

        return buffer

    def send_at(self, command, timeout=5000):
        self.clear_input()
        self.gsm.write(command + "\r\n")

        response = self.read_until((b"OK",), timeout)

        return b"OK" in response and b"ERROR" not in response

    def check_network(self):
        self.clear_input()
        self.gsm.write("AT+CREG?\r\n")

        response = self.read_until(
            (b"+CREG:", b"OK"), 5000
        )

        if b"OK" not in response or b"ERROR" in response:
            return False

        text = response.decode("utf-8", "ignore")

        for line in text.splitlines():
            if "+CREG:" in line:
                try:
                    fields = line.split(":", 1)[1].strip().split(",")
                    return fields[-1].strip() in ("1", "5")
                except Exception:
                    return False

        return False

    def record_failure(self, reason):
        global sms_failure_backoff, sms_failure_count

        sms_failure_count += 1
        delay = 60000 * min(sms_failure_count, 5)

        sms_failure_backoff = time.ticks_add(
            time.ticks_ms(), delay
        )

        print("SMS failed:", reason)

    def send_sms(self, phone_number, message, severity):
        global sms_cooldown_warning
        global sms_cooldown_danger
        global sms_failure_count

        now = time.ticks_ms()

        cooldown = (
            sms_cooldown_danger
            if severity == "Danger"
            else sms_cooldown_warning
        )

        if time.ticks_diff(cooldown, now) > 0:
            return False

        if time.ticks_diff(sms_failure_backoff, now) > 0:
            return False

        try:
            if not self.check_network():
                self.record_failure("modem not registered")
                return False

            if not self.send_at("AT"):
                self.record_failure("no AT response")
                return False

            if not self.send_at("AT+CMGF=1"):
                self.record_failure("cannot set text mode")
                return False

            self.clear_input()
            self.gsm.write(
                'AT+CMGS="{}"\r\n'.format(phone_number)
            )

            prompt = self.read_until((b">",), 10000)

            if b">" not in prompt or b"ERROR" in prompt:
                self.record_failure("no SMS prompt")
                return False

            self.gsm.write(message.encode())
            self.gsm.write(bytes([26]))

            response = self.read_until(
                (b"+CMGS:", b"OK"), 40000
            )

            if (
                b"+CMGS:" not in response
                or b"OK" not in response
                or b"ERROR" in response
            ):
                self.record_failure("SMS confirmation failed")
                return False

            sms_failure_count = 0

            deadline = time.ticks_add(
                time.ticks_ms(), SMS_COOLDOWN_MS
            )

            if severity == "Danger":
                sms_cooldown_danger = deadline
            else:
                sms_cooldown_warning = deadline

            print(severity, "SMS sent")
            return True

        except Exception as error:
            self.record_failure(str(error))
            return False


sms_system = SmsSystem()


def handle_sms(overall_status):
    if overall_status == "Danger":
        sms_system.danger_count += 1
        sms_system.warning_count = 0

        if sms_system.danger_count >= 4:
            sent = sms_system.send_sms(
                SMS_PHONE,
                "DANGER: Coastline tracker reports danger status. "
                "Check the site.",
                "Danger"
            )

            if sent:
                sms_system.danger_count = 0

    elif overall_status == "Warning":
        sms_system.warning_count += 1
        sms_system.danger_count = 0

        if sms_system.warning_count >= 5:
            sent = sms_system.send_sms(
                SMS_PHONE,
                "WARNING: Coastline tracker reports abnormal "
                "readings. Please inspect the site.",
                "Warning"
            )

            if sent:
                sms_system.warning_count = 0

    else:
        sms_system.danger_count = 0
        sms_system.warning_count = 0


def post_data(
    wlan,
    ph_value,
    do_value,
    h2s_value,
    ph_raw,
    do_raw,
    h2s_raw,
    ph_status,
    do_status,
    h2s_status,
    overall_status,
    simulated
):
    if not wlan.isconnected():
        print("Skipping POST: Wi-Fi disconnected")
        return

    data = {
        "station_id": STATION_ID,
        "ph": ph_value,
        "do": do_value,
        "h2s": h2s_value,
        "nh3": 0.0,
        "do_unit": "mg/L",
        "h2s_unit": (
            "simulated ppm"
            if simulated
            else "estimated test scale; not calibrated ppm"
        ),
        "ph_adc": ph_raw,
        "do_adc": do_raw,
        "h2s_adc": h2s_raw,
        "nh3_adc": 0,
        "do_calibrated": False,
        "h2s_calibrated": False,
        "nh3_calibrated": False,
        "ph_status": ph_status,
        "do_status": do_status,
        "h2s_status": h2s_status,
        "nh3_status": "Unavailable",
        "overall_status": overall_status,
        "status": overall_status,
        "statuses": overall_status,
        "simulation_active": simulated,
        "reading_mode": (
            "SIMULATED"
            if simulated
            else "LIVE SENSOR"
        ),
        "remote_override_status": (
            remote_override_status or "None"
        )
    }

    if clock_synced:
        data["timestamp"] = time.time() + 946684800

    response = None

    try:
        response = urequests.post(SERVER_URL, json=data)

        if 200 <= response.status_code < 300:
            print(
                "Dashboard updated:",
                data["reading_mode"],
                overall_status
            )
        else:
            print("Dashboard response:", response.status_code)

    except Exception as error:
        print("Dashboard POST failed:", error)

    finally:
        if response is not None:
            response.close()

        gc.collect()


def main():
    global sms_cooldown_warning, sms_cooldown_danger

    print("Coastline Water Quality Tracker")

    wlan = connect_wifi()

    if wlan.isconnected():
        sync_clock()

    radio = setup_espnow()

    now = time.ticks_ms()

    sms_cooldown_warning = time.ticks_add(
        now, -SMS_COOLDOWN_MS
    )
    sms_cooldown_danger = time.ticks_add(
        now, -SMS_COOLDOWN_MS
    )

    last_read = None
    last_wifi_retry = time.ticks_ms()
    sensor_overall_status = "Normal"

    while True:
        now = time.ticks_ms()

        process_remote_commands(radio)

        if (
            last_read is None
            or time.ticks_diff(now, last_read) >= READ_INTERVAL_MS
        ):
            last_read = now

            try:
                # Always collect the real sensor readings.
                ph_raw, real_ph = read_ph()
                do_raw, real_do = read_do()
                h2s_raw, real_h2s = read_h2s()

                real_ph_status = get_ph_status(real_ph)
                real_do_status = get_do_status(real_do)
                real_h2s_status = get_h2s_status(real_h2s)

                sensor_overall_status = get_overall_status(
                    real_ph_status,
                    real_do_status,
                    real_h2s_status
                )

                simulated = remote_override_status is not None

                if simulated:
                    # Publish simulated readings while an override is active.
                    demo = SIMULATED_READINGS[remote_override_status]

                    ph_value = demo["ph"]
                    do_value = demo["do"]
                    h2s_value = demo["h2s"]

                    ph_status = get_ph_status(ph_value)
                    do_status = get_do_status(do_value)
                    h2s_status = get_h2s_status(h2s_value)

                    overall_status = remote_override_status

                else:
                    # Return to actual sensor readings.
                    ph_value = real_ph
                    do_value = real_do
                    h2s_value = real_h2s

                    ph_status = real_ph_status
                    do_status = real_do_status
                    h2s_status = real_h2s_status

                    overall_status = sensor_overall_status

                print("Reading mode:", "SIMULATED" if simulated else "LIVE SENSOR")
                print("pH:", ph_value, "|", ph_status)
                print("DO:", do_value, "mg/L |", do_status)
                print("H2S:", h2s_value, "|", h2s_status)
                print("Overall:", overall_status)
                print("--------------------")

                alert_system.update(overall_status)
                handle_sms(overall_status)

                if not wlan.isconnected():
                    if time.ticks_diff(now, last_wifi_retry) >= 30000:
                        print("Reconnecting Wi-Fi...")
                        wlan = connect_wifi()
                        last_wifi_retry = time.ticks_ms()

                        if wlan.isconnected() and not clock_synced:
                            sync_clock()
                else:
                    last_wifi_retry = now

                post_data(
                    wlan,
                    ph_value,
                    do_value,
                    h2s_value,
                    ph_raw,
                    do_raw,
                    h2s_raw,
                    ph_status,
                    do_status,
                    h2s_status,
                    overall_status,
                    simulated
                )

            except Exception as error:
                print("Reading cycle error:", error)
                gc.collect()

        process_remote_commands(radio)
        time.sleep_ms(20)


try:
    main()

except KeyboardInterrupt:
    print("Stopping tracker")
    red.off()
    green.off()
    blue.off()