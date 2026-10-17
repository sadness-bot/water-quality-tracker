from machine import Pin
import time

red = Pin(13, Pin.OUT)
blue = Pin(12, Pin.OUT)
green = Pin(14, Pin.OUT)

red.off()
blue.off()
green.off()