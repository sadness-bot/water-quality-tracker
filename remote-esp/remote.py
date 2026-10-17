from machine import Pin
import time
import network
import espnow
from ir_rx.nec import NEC_8

WIFI_SSID = "Sargassum-Dashboard"
WIFI_PASSWORD = "Sargassum2026"
IR_PIN = 23

BROADCAST = b"\xff\xff\xff\xff\xff\xff"

BUTTON_COMMANDS = {
    0x0C: ("NORMAL", b"COASTLINE:NORMAL"),
    0x18: ("WARNING", b"COASTLINE:WARNING"),
    0x5E: ("DANGER", b"COASTLINE:DANGER"),
    0x08: ("CANCEL", b"COASTLINE:CANCEL"),
}

pending_code = -1
last_code = -1
last_sent_ms = 0


def ir_callback(data, addr, ctrl):
    global pending_code

    if data >= 0:
        pending_code = data


def connect_wifi(wlan, timeout_ms=15000):
    wlan.active(True)

    try:
        wlan.config(pm=wlan.PM_NONE)
    except Exception:
        pass

    if wlan.isconnected():
        return True

    try:
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    except Exception as error:
        print("Wi-Fi connection error:", error)
        return False

    start = time.ticks_ms()

    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) >= timeout_ms:
            return False

        time.sleep_ms(250)

    return True


def main():
    global pending_code, last_code, last_sent_ms

    wlan = network.WLAN(network.STA_IF)
    connected = connect_wifi(wlan)

    if connected:
        print("Wi-Fi connected:", wlan.ifconfig()[0])

        try:
            wlan.config(pm=wlan.PM_NONE)
        except Exception:
            pass
    else:
        print("Wi-Fi not connected.")
        print("Check that Sargassum-Dashboard is running.")

    radio = espnow.ESPNow()
    radio.active(True)

    try:
        radio.add_peer(BROADCAST)
    except Exception as error:
        print("Broadcast peer setup:", error)

    ir = NEC_8(Pin(IR_PIN, Pin.IN), ir_callback)

    print("Remote controller ready.")
    print("1 = Normal")
    print("2 = Warning")
    print("3 = Danger")
    print("4 = Cancel override")

    last_wifi_retry = time.ticks_ms()

    while True:
        now = time.ticks_ms()

        if (
            not wlan.isconnected()
            and time.ticks_diff(now, last_wifi_retry) >= 10000
        ):
            last_wifi_retry = now

            print("Reconnecting Wi-Fi...")
            connected = connect_wifi(wlan, 5000)

            if connected:
                print("Wi-Fi reconnected:", wlan.ifconfig()[0])

                try:
                    wlan.config(pm=wlan.PM_NONE)
                except Exception:
                    pass

        code = pending_code

        if code >= 0:
            pending_code = -1

            if (
                code == last_code
                and time.ticks_diff(now, last_sent_ms) < 250
            ):
                time.sleep_ms(10)
                continue

            last_code = code
            last_sent_ms = now

            command = BUTTON_COMMANDS.get(code)

            if command is None:
                print("Unassigned button: 0x{:02X}".format(code))
            elif not wlan.isconnected():
                print("Wi-Fi disconnected; command not sent.")
            else:
                name, packet = command

                try:
                    result = radio.send(BROADCAST, packet, True)

                    if result:
                        print("Sent:", name)
                    else:
                        print("Send failed:", name)

                except Exception as error:
                    print("Transmission error:", error)

        time.sleep_ms(10)


try:
    main()
except KeyboardInterrupt:
    print("Remote controller stopped.")