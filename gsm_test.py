
from machine import UART
import time

gsm = UART(2, baudrate=9600, tx=17, rx=16, timeout=2000)

commands = ["AT", "AT+CPIN?", "AT+CREG?", "AT+CSQ"]

for command in commands:
    print("\nSending:", command)
    gsm.write(command + "\r\n")
    time.sleep(3)

    response = gsm.read()
    print(response.decode("utf-8", "ignore") if response else "No response")

    time.sleep(2)

