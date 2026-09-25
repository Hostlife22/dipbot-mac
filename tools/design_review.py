"""Visible offline layout review; isolated state, no network or wallet."""
import argparse,json,math,tempfile,time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QScrollArea
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QEvent, QPointF
from PySide6.QtGui import QMouseEvent
from dipbot.app import Window,STYLE
from dipbot.worker import Worker
from dipbot.storage import Store


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setStyleSheet(STYLE)
    results=[]
    with tempfile.TemporaryDirectory() as directory, patch.object(Worker,'start',lambda _:None):
        w=Window(Store(Path(directory)/'state.json'));w.show()
        def pump():
            for _ in range(8):app.processEvents();time.sleep(.01)
        for size in [(1180,850),(940,700)]:
            w.resize(*size)
            for index in range(w.tabs.count()):
                w.tabs.setCurrentIndex(index);pump()
                scroll=w.tabs.widget(index)
                results.append({'size':list(size),'tab':w.tabs.tabText(index),
                    'horizontal_scroll':scroll.horizontalScrollBar().maximum(),
                    'stop_visible':w.stop.isVisible(),
                    'stop_inside_window':w.rect().contains(w.stop.mapTo(w,w.stop.rect().bottomRight()))})
                w.grab().save(str(output/f'{size[0]}-tab-{index}.png'))
                if index==0:
                    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum());pump()
                    w.grab().save(str(output/f'{size[0]}-trade-bottom.png'))
                    scroll.verticalScrollBar().setValue(0)
        w.resize(1180,850);w.tabs.setCurrentIndex(0)
        now=time.monotonic()
        w.chart.values.extend(1+.02*math.sin(i/8) for i in range(100))
        w.chart.times.extend(now-10+i*.1 for i in range(100))
        w.on_event('status',{'mode':'DEMO','running':True,'locked':False,'position':'12.3456789',
            'base':'1','entry':'1','levels':{'ENTRY':'1','TP':'1.02','SL':'.98'},'realized':'0'})
        w.on_event('price', '1.005')
        w.chart.markers.append((w.chart.times[20], 'BUY', w.chart.values[20]))
        w.chart.markers.append((w.chart.times[70], 'SELL', w.chart.values[70]))
        pump();w.grab().save(str(output/'demo-position.png'))
        w.raise_();w.activateWindow();app.setActiveWindow(w);pump()
        pos = QPointF(w.chart.rect().center())
        app.sendEvent(w.chart, QMouseEvent(QEvent.MouseMove, pos,
            QPointF(w.chart.mapToGlobal(pos.toPoint())), Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        pump()
        assert w.chart.hover is not None
        w.grab().save(str(output/'chart-hover.png'))
        w.on_event('status',{'mode':'DEMO','running':False,'locked':False,'position':'0',
            'base':'0','entry':'0','levels':{},'realized':'0'})
        w.market_toggle.setChecked(True);pump()
        w.grab().save(str(output/'expanded-settings.png'))
        assert w.params['slippage'].isVisible()
        w.market_toggle.setChecked(False)
        w.tabs.widget(0).verticalScrollBar().setValue(0)
        w.mode.setCurrentText('LIVE')
        w.on_event('status',{'mode':'LIVE','running':False,'locked':False,'position':'0',
            'base':'0','entry':'0','levels':{},'realized':'—'})
        w.raise_();w.activateWindow();app.setActiveWindow(w);pump()
        w.journal_toggle.setFocus();QTest.keyClick(w.journal_toggle,Qt.Key_Space);pump()
        assert w.activity.isVisible()
        w.mode.setFocus();QTest.keyClick(w.mode,Qt.Key_Tab);pump()
        assert app.focusWidget() is not None
        w.grab().save(str(output/'live-mode-journal.png'))
        w.worker.quit_event.set();w.close()
    (output/'report.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
    assert all(r['horizontal_scroll']==0 and r['stop_inside_window'] for r in results),results
    print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
