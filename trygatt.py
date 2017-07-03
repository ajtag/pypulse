#!/usr/bin/python3
import gatt
import datetime
import pprint
import threading
from queue import Queue
import inspect

import logging

import sys

pulse_uuid = {
        'service':'00005301-0000-0041-4c50-574953450000',
        'write':'00005302-0000-0041-4c50-574953450000',
        'read':'00005303-0000-0041-4c50-574953450000',
        'notice':'0002902-0000-1000-8000-00805f9b34fb'
}
notifications = '0002902-0000-1000-8000-00805f9b34fb'



class AnyDeviceManager(gatt.DeviceManager):
    """
    An implementation of ``gatt.DeviceManager`` that discovers any GATT device
    and prints all discovered devices.
    """

    def device_discovered(self, device):
        print("%s [%s] Discovered, alias = %s" % (device, device.mac_address, device.alias()))

    def make_device(self, mac_address):
        return Pulse(mac_address=mac_address, manager=self)



class Pulse(gatt.Device):
    def __init__(self, mac_address, manager, auto_reconnect=False):
        super().__init__(mac_address=mac_address, manager=manager)
        self.auto_reconnect = auto_reconnect
        self.log = logging.getLogger('BLE_Pulse_Device')
        self.log.setLevel(logging.DEBUG)

        self.memory = bytearray(15*24)
        self.dmem = {'day':{}, 'pulse':None, 'pulsedate':None}


        self.message_queue = Queue()
        self.last_sent = None

        self.comms_free = threading.Event()
        self.pulse_busy()

        self.running = True
        self.dispatchT = threading.Thread(target=self.dispatch)
        self.dispatchT.setDaemon(True)
        self.dispatchT.start()

    def pulse_free(self):
        self.log.warning('{} set pulse available'.format(inspect.stack()[1][3]))
        self.comms_free.set()


    def pulse_busy(self):
        self.log.warning('{} set pulse busy'.format(inspect.stack()[1][3]))
        self.comms_free.clear()

    def dispatch(self):
        log = self.log.getChild('dispatcher')
        while True:
            value = self.message_queue.get()
            log.info('sending {} to busy pulse?.. {}'.format(' '.join(['0x{0:x}'.format(s) for s in value]), self.comms_free.is_set()))
            self.comms_free.wait(None)
            self.last_sent = value
            self.pulse_busy()
            self.write_ch.write_value(value)
            self.message_queue.task_done()



    def connect(self):
        self.log.info("Connecting...")
        super().connect()


    def connect_succeeded(self):
        super().connect_succeeded()
        # print("[%s] Connected" % (self.mac_address))
        self.log = logging.getLogger('BLE_Pulse_Device.{}'.format(self.mac_address))
        self.log.setLevel(logging.DEBUG)


    def connect_failed(self, error):
        super().connect_failed(error)
        self.log.warn("[%s] Connection failed: %s" % (self.mac_address, str(error)))


    def disconnect_succeeded(self):
        super().disconnect_succeeded()
        self.log = logging.getLogger('BLE_PulseDevice')

        self.log.info("[%s] Disconnected" % (self.mac_address))
        if self.auto_reconnect:
            self.connect()

    def services_resolved(self):
        super().services_resolved()

        self.log.debug('got service list')

        self.service = next( s for s in self.services if s.uuid == pulse_uuid['service'])
        chars = self.service.characteristics

        self.notice = gatt.Characteristic(service=self.service,
                                      path='/org/bluez/hci0/dev_C8_FD_19_11_72_06/service0010/char0013',
                                      uuid='00002902-0000-1000-8000-00805f9b34fb')
        self.read_ch = next(s for s in chars if s.uuid == pulse_uuid['read'])
        self.write_ch = next(s for s in chars if s.uuid == pulse_uuid['write'])


        self.pulse_busy()
        self.notice.enable_notifications(True)





    def get_all(self):
        self.get_summary()
        self.get_pulsedate()

        for i in range(1, 15):
            self.get_daily(i)
        self.message_queue.join()
        for day_index in self.dmem['day']:
            if self.dmem['day'][day_index]:
                day = self.dmem['day'][day_index]
                for hour in range(1, 25):
                    try:
                        day['hour'][hour] = day['hour'].get(hour, {})
                        self.get_hourly(day['index'], hour)
                    except TypeError:
                        pass



    def get_summary(self): # pulse_summary
        # return an 0x81 result
        self.log.info('getting summary info')
        self.dmem['pulse'] = False
        self.message_queue.put([0x34, 0x00, 0x00, 0x00])

    def get_pulsedate(self): # calendardate set on pulse
        self.log.info('getting date info')
        self.dmem['pulsedate'] = False
        self.message_queue.put([0x34, 0x03, 0x00, 0x00])

    def get_daily(self, index):
        # get a 0x82
        self.log.info('get day summary for index: {}'.format(index))
        self.dmem['day'][index] = False
        self.message_queue.put([0x34, 0x01, index, 0x00])

    def get_hourly(self, index, hour):
        # if index == 0:
        #     #self.log.
        #     self.get

        # gets 0x86
        self.log.info('get daily for index: {} at {}:00'.format(index, hour))
        if self.dmem['day'][index]:
            self.dmem['day'][index]['hour'][hour] = False
            self.message_queue.put([0x34, 0x03, index, hour])

    # public class ble_set_time {
    #     public static void main(String[] args){
    #         Calendar cal = Calendar.getInstance();
    #
    #         int iyear = cal.get(1);
    #         String syear  = String.valueOf(iyear);
    #
    #         String year_v7 = syear.substring(2, 4);
    #         int year_v8 = Integer.parseInt(year_v7);
    #         byte year = (byte)year_v8;
    #
    #
    #         byte month = (byte)(cal.get(2) + 1);
    #
    #         byte day = (byte)cal.get(5);
    #
    #         byte hour = (byte)cal.get(11);
    #
    #         byte minute = (byte)cal.get(12);
    #
    #         byte second = (byte)cal.get(13);
    #
    # //        BluetoothGattCharacteristic
    # byte[] data = new byte[] { 0x31, second, minute, hour, day, month, year};
    #
    #
    #         System.out.printf("%s", Arrays.toString(data));
    #         }
    # }

    def reset_datetime(self):
        self.log.warning('RESETTING DATETIME ON PULSE... DATA WILL BE LOST')
        now = datetime.datetime.now()
        self.message_queue.put([0x31, now.year % 2000, now.month, now.day, now.minute, now.second])


    def parse(self, last, val):
        data = None
        msgtype = val[0]
        logging.basicConfig()
        log = logging.getLogger('parser.%02x' % (msgtype,))
        log.setLevel(logging.DEBUG)
        log.debug('parse')

        data = {'last': last, 'result': 'success'}

        if msgtype == 0x81: # pulse
            # memory
            # day
            # days
            # battery
            self.log.info('battery at {:.1%}'.format(val[3] / 255))
            data = {'days': val[1], 'IntensityDays': val[2], 'battery': val[3]}
            self.dmem['pulse'] = data

        elif msgtype == 0x82: # hour_summary
            #     idx  day mon year minl minh  step0 1  2  3  reserved1 2 3 4   ?
            # 82  05   04  01   ff  2a   00    d2 15 00       00 00 00 00       00
            data['index'] = val[1]
            data['day'] = val[2]
            data['month'] = val[3]
            data['year'] = 2000 + val[4]
            data['activity'] = join_2(val[5], val[6])
            data['steps'] = join_4(val[7], val[8], val[9], val[10])
            data['calories'] = join_2(val[11], val[12])
            data['exTravelled'] = join_2(val[13], val[14])
            data['hour'] = {}
            self.dmem['day'][data['index']] = data
            # log.info()
            # day_info

        elif msgtype == 0x85: # pulsedate
            data['index'] = val[1]
            data['day'] = val[2]
            data['month'] = val[3]
            data['year'] = 2000 + val[4]
            self.dmem['pulsedate'] = data

            # currentIntensityIndex
            # currentIntensityDate

        elif msgtype == 0x86: #hour_summary
            # intensity
            data['index'] = val[1]
            data['hour'] = val[2]
            data['steps'] = (
                join_2(val[3], val[4]),
                join_2(val[6], val[7]),
                join_2(val[9], val[10]),
                join_2(val[12], val[13])
            )

            data['time_active'] = (val[5], val[8], val[11], val[14])

            self.dmem['day'][data['index']]['hour'][data['hour']] = data

        elif msgtype == 0x90:
            data['result'] = 'fail'

        elif msgtype == 0x91:
            self.log.warning('datetime reset on device')


        return data

    def characteristic_enable_notifications_succeeded(self, characteristic):
        """
        Called when a characteristic notifications enable command succeeded.
        """
        self.log.info('notifications enabled on {}'.format(characteristic.uuid))
        self.pulse_free()



    def characteristic_value_updated(self, characteristic, value):
        """
        Called when a characteristic value has changed.
        """
        last = self.last_sent
        self.pulse_free()
        self.log.info('{}, {}, {}'.format(characteristic.uuid, 'char value updated', value))
        self.log.info(pprint.pformat(self.parse(last, value)))



        # try:
        #     print('[{}] updated to: {}'.format(characteristic, ':'.join("{:02x}".format(ord(c)) for c in value)))
        # except:
        #     pass


    def characteristic_write_value_succeeded(self, characteristic):
        """
        Called when a characteristic value write command succeeded.
        """
        self.log.info('write to {} successfull'.format(characteristic.uuid))

        #self.pulse_free()

    def characteristic_read_value_failed(self, characteristic, error):
        """
        Called when a characteristic value read command failed.
        """
        self.log.info('read failed for {}, {}'.format(characteristic.uuid, error))

        self.pulse_free()


    def characteristic_write_value_failed(self, characteristic, error):
        """
        Called when a characteristic value write command failed.
        """
        self.log.info('write failed {} {} {}'.format(characteristic.uuid, 'write value failed', error))

        self.pulse_free()



    def characteristic_enable_notifications_failed(self, characteristic, error):
        """
        Called when a characteristic notifications enable command failed.
        """
        self.log.info('{} {} {}'.format(characteristic.uuid, 'enable notifications failed', error))

        self.pulse_free()



def join_2(word1, word2):
    return (0x100 * word2 + word1)


def join_4(word1, word2, word3, word4):
    return(0x1000000 * word4 + 0x10000 * word3 + 0x100 * word2 + word1)



if __name__ == '__main__':

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)
    log = logging.getLogger('MAIN')

    import configparser
    cfg = configparser.ConfigParser()
    cfg.read('pulse.conf')
    MAC = cfg.get('pulse', 'MAC')
    if MAC == 'XX:XX:XX:XX:XX:XX':
        logging.getLogger('Config').error('you must provide a MAC address for your pulse')
        sys.exit(1)






    manager = AnyDeviceManager(adapter_name='hci0')
    device = Pulse(mac_address=MAC, manager=manager)

    log.info('''pulse must be in sync mode..
     to get it into sync mode you have to set the time(not implemented yet)
     Waiting for connection...''')

    device.connect()

    log.info("Terminate with Ctrl+C")
    try:
        t = threading.Thread(target=manager.run)
        t.start()


    except KeyboardInterrupt:
        pass

    device.get_all()
    device.message_queue.join()
    
    
    # import json
    # pprint.pprint(device.dmem)
    # with open('dump.json', 'w') as fh:
    #     json.dump(device.dmem, fh, indent=2)
    # device.disconnect()
    # manager.stop()
    # t.join()
