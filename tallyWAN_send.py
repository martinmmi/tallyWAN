import gc
import esp32
import ssd1306

from machine import Pin, I2C, SPI
from time import time

TX_BASE_ADDR = 0x00
RX_BASE_ADDR = 0x00

PA_BOOST = 0x80
PA_OUTPUT_RFO_PIN = 0
PA_OUTPUT_PA_BOOST_PIN = 1

REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FRF_MSB = 0x06
REG_FRF_MID = 0x07
REG_FRF_LSB = 0x08
REG_PA_CONFIG = 0x09
REG_LNA = 0x0c
REG_FIFO_ADDR_PTR = 0x0d
REG_FIFO_TX_BASE_ADDR = 0x0e
REG_FIFO_RX_BASE_ADDR = 0x0f
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_IRQ_FLAGS = 0x12
REG_RX_NB_BYTES = 0x13
REG_PKT_RSSI_VALUE = 0x1a
REG_PKT_SNR_VALUE = 0x1b
REG_MODEM_CONFIG_1 = 0x1d
REG_MODEM_CONFIG_2 = 0x1e
REG_PREAMBLE_MSB = 0x20
REG_PREAMBLE_LSB = 0x21
REG_PAYLOAD_LENGTH = 0x22
REG_MODEM_CONFIG_3 = 0x26
REG_DETECTION_OPTIMIZE = 0x31
REG_DETECTION_THRESHOLD = 0x37
REG_SYNC_WORD = 0x39
REG_DIO_MAPPING_1 = 0x40
REG_VERSION = 0x42

MODE_LORA = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_TX = 0x03
MODE_RX_CONTINUOUS = 0x05

IRQ_TX_DONE_MASK = 0x08
IRQ_PAYLOAD_CRC_ERROR_MASK = 0x20

MAX_PKT_LENGTH = 255

class LoRa:

    def __init__(self, spi, **kw):
        self.spi = spi
        self.cs = kw['cs']
        self.rx = kw['rx']
        if self._read(REG_VERSION) != 0x12:
            raise Exception('Invalid version or bad SPI connection')
        self.sleep()
        self.set_frequency(kw.get('frequency', 915.0))
        self.set_bandwidth(kw.get('bandwidth', 250000))
        self.set_spreading_factor(kw.get('spreading_factor', 10))
        self.set_coding_rate(kw.get('coding_rate', 5))
        self.set_preamble_length(kw.get('preamble_length', 4))
        self.set_crc(kw.get('crc', False))
        # set LNA boost
        self._write(REG_LNA, self._read(REG_LNA) | 0x03)
        # set auto AGC
        self._write(REG_MODEM_CONFIG_3, 0x04)
        self.set_tx_power(kw.get('tx_power', 24))
        self._implicit = kw.get('implicit', False)
        self.set_implicit(self._implicit)
        self.set_sync_word(kw.get('sync_word', 0x12))
        self._on_recv = kw.get('on_recv', None)
        self._write(REG_FIFO_TX_BASE_ADDR, TX_BASE_ADDR)
        self._write(REG_FIFO_RX_BASE_ADDR, RX_BASE_ADDR)
        self.standby()

    def begin_packet(self):
        self.standby()
        self._write(REG_FIFO_ADDR_PTR, TX_BASE_ADDR)
        self._write(REG_PAYLOAD_LENGTH, 0)

    def end_packet(self):
        self._write(REG_OP_MODE, MODE_LORA | MODE_TX)
        while (self._read(REG_IRQ_FLAGS) & IRQ_TX_DONE_MASK) == 0:
            pass
        self._write(REG_IRQ_FLAGS, IRQ_TX_DONE_MASK)
        gc.collect()

    def write_packet(self, b):
        n = self._read(REG_PAYLOAD_LENGTH)
        m = len(b)
        p = MAX_PKT_LENGTH - TX_BASE_ADDR
        if n + m > p:
            raise ValueError('Max payload length is ' + str(p))
        for i in range(m):
            self._write(REG_FIFO, b[i])
        self._write(REG_PAYLOAD_LENGTH, n + m)

    def send(self, x):
        if isinstance(x, str):
            x = x.encode()
        self.begin_packet()
        self.write_packet(x)
        self.end_packet()

    def _get_irq_flags(self):
        f = self._read(REG_IRQ_FLAGS)
        self._write(REG_IRQ_FLAGS, f)
        return f

    def get_rssi(self):
        rssi = self._read(REG_PKT_RSSI_VALUE)
        if self._frequency >= 779.0:
            return rssi - 157
        return rssi - 164

    def get_snr(self):
        return self._read(REG_PKT_SNR_VALUE) * 0.25

    def standby(self):
        self._write(REG_OP_MODE, MODE_LORA | MODE_STDBY)

    def sleep(self):
        self._write(REG_OP_MODE, MODE_LORA | MODE_SLEEP)

    def set_tx_power(self, level, outputPin=PA_OUTPUT_PA_BOOST_PIN):
        if outputPin == PA_OUTPUT_RFO_PIN:
            level = min(max(level, 0), 14)
            self._write(REG_PA_CONFIG, 0x70 | level)
        else:
            level = min(max(level, 2), 17)
            self._write(REG_PA_CONFIG, PA_BOOST | (level - 2))

    def set_frequency(self, frequency):
        self._frequency = frequency
        hz = frequency * 1000000.0
        x = round(hz / 61.03515625)
        self._write(REG_FRF_MSB, (x >> 16) & 0xff)
        self._write(REG_FRF_MID, (x >> 8) & 0xff)
        self._write(REG_FRF_LSB, x & 0xff)

    def set_spreading_factor(self, sf):
        if sf < 6 or sf > 12:
            raise ValueError('Spreading factor must be between 6-12')
        self._write(REG_DETECTION_OPTIMIZE, 0xc5 if sf == 6 else 0xc3)
        self._write(REG_DETECTION_THRESHOLD, 0x0c if sf == 6 else 0x0a)
        reg2 = self._read(REG_MODEM_CONFIG_2)
        self._write(REG_MODEM_CONFIG_2, (reg2 & 0x0f) | ((sf << 4) & 0xf0))

    def set_bandwidth(self, bw):
        self._bandwidth = bw
        bws = (7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000)
        i = 9
        for j in range(len(bws)):
            if bw <= bws[j]:
                i = j
                break
        x = self._read(REG_MODEM_CONFIG_1) & 0x0f
        self._write(REG_MODEM_CONFIG_1, x | (i << 4))

    def set_coding_rate(self, denom):
        denom = min(max(denom, 5), 8)
        cr = denom - 4
        reg1 = self._read(REG_MODEM_CONFIG_1)
        self._write(REG_MODEM_CONFIG_1, (reg1 & 0xf1) | (cr << 1))

    def set_preamble_length(self, n):
        self._write(REG_PREAMBLE_MSB, (n >> 8) & 0xff)
        self._write(REG_PREAMBLE_LSB, (n >> 0) & 0xff)

    def set_crc(self, crc=False):
        modem_config_2 = self._read(REG_MODEM_CONFIG_2)
        if crc:
            config = modem_config_2 | 0x04
        else:
            config = modem_config_2 & 0xfb
        self._write(REG_MODEM_CONFIG_2, config)

    def set_sync_word(self, sw):
        self._write(REG_SYNC_WORD, sw)

    def set_implicit(self, implicit=False):
        if self._implicit != implicit:
            self._implicit = implicit
            modem_config_1 = self._read(REG_MODEM_CONFIG_1)
            if implicit:
                config = modem_config_1 | 0x01
            else:
                config = modem_config_1 & 0xfe
            self._write(REG_MODEM_CONFIG_1, config)

    def on_recv(self, callback):
        self._on_recv = callback
        if self.rx:
            if callback:
                self._write(REG_DIO_MAPPING_1, 0x00)
                self.rx.irq(handler=self._irq_recv, trigger=Pin.IRQ_RISING)
            else:
                self.rx.irq(handler=None, trigger=0)

    def recv(self):
        self._write(REG_OP_MODE, MODE_LORA | MODE_RX_CONTINUOUS)

    def _irq_recv(self, event_source):
        f = self._get_irq_flags()
        if f & IRQ_PAYLOAD_CRC_ERROR_MASK == 0:
            if self._on_recv:
                self._on_recv(self._read_payload())

    def _read_payload(self):
        self._write(REG_FIFO_ADDR_PTR, self._read(REG_FIFO_RX_CURRENT_ADDR))
        if self._implicit:
            n = self._read(REG_PAYLOAD_LENGTH)
        else:
            n = self._read(REG_RX_NB_BYTES)
        payload = bytearray()
        for i in range(n):
            payload.append(self._read(REG_FIFO))
        gc.collect()
        return bytes(payload)

    def _transfer(self, addr, x=0x00):
        resp = bytearray(1)
        self.cs.value(0)
        self.spi.write(bytes([addr]))
        self.spi.write_readinto(bytes([x]), resp)
        self.cs.value(1)
        return resp

    def _read(self, addr):
        x = self._transfer(addr & 0x7f)
        return int.from_bytes(x, 'big')

    def _write(self, addr, x):
        self._transfer(addr | 0x80, x)

# I2C pins
i2c = I2C(sda=Pin(21), scl=Pin(22), freq=100000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)
devices = i2c.scan()                               # len(devices)           hex(device)

# Output-Input pins
p25 = Pin(25, Pin.OUT)
p14 = Pin(14, Pin.OUT)
p35 = Pin(35, Pin.IN)

# SPI pins
SCK  = 5
MOSI = 27
MISO = 19

# Chip select
CS   = 18

# Receive IRQ
RX   = 26

# Setup some Variables
last_clk_state = 0
clk_state = 0
zStempel = int(time())
lastMillis1 = 0
lastMillis1 = int(time())
minutes = 0
timer = 0
temp_c = 0
hall = 0
message = 0

# Setup SPI
spi = SPI(
    1,
    baudrate=10000000,
    sck=Pin(SCK, Pin.OUT, Pin.PULL_DOWN),
    mosi=Pin(MOSI, Pin.OUT, Pin.PULL_UP),
    miso=Pin(MISO, Pin.IN, Pin.PULL_UP),
)
spi.init()

# Setup LoRa
lora = LoRa(
    spi,
    cs=Pin(CS, Pin.OUT),
    rx=Pin(RX, Pin.IN),
    frequency=868.0,
    bandwidth=250000,
    spreading_factor=10,
    coding_rate=5,
)

while True:

    #CLOCK
    if clk_state == 0 and int(time()) - zStempel < 1:
        clk_state = 1

    if clk_state == 1 and int(time()) - zStempel >= 1:
        if int(time()) - zStempel <= 2:
            clk_state = 0

    if last_clk_state != clk_state:
        last_clk_state = clk_state
        p14.value(clk_state)
        p25.value(clk_state)

        if clk_state == 1:
            message += 1
            converted_message = str(message)
            lora.send(converted_message)
            print("SEND: ")
            print(converted_message)

    if int(time()) - zStempel >= 2:
        zStempel = int(time())

    #MILLIS CLOCK
    if int(time()) - lastMillis1 >= 1:
        temp = esp32.raw_temperature()  # read the internal temperature of the MCU, in Fahrenheit
        temp_c = ((temp - 32) * 5 / 9)

        hall = esp32.hall_sensor()

        lastMillis1 = int(time())

    converted_temp_c = str(temp_c)
    converted_hall = str(hall)
    converted_clk_state = str(clk_state)

    minutes = int(time()/60)
    converted_minutes = str(minutes)

    display.contrast(255)
    display.invert(0)
    display.fill(0)
    display.text('TallyWAN', 0, 0, 1)
    display.text("CLK", 88, 0, 1)
    display.text(converted_clk_state, 115, 0, 1)
    display.text("SEND:", 0, 24, 1)
    display.text(converted_message, 50, 24, 1)
    display.text("Temp: ", 0, 36, 1)
    display.text(converted_temp_c, 50, 36, 1)
    display.text("Time: ", 0, 48, 1)
    display.text(converted_minutes, 50, 48, 1)
    display.text("min", 80, 48, 1)
    display.show()