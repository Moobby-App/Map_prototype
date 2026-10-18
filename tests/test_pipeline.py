import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from scrapers import krl_client as client
from scrapers.common import active
from scrapers.scrape_krl import normalize
from scrapers.fetch_osm import convert

class PipelineTests(unittest.TestCase):
    def test_verified_empty_schedule_404_only(self):
        response = Mock(status_code=404, text='{"status":404,"data":"Data not found"}', headers={"content-type": "application/json"})
        response.json.return_value = {"status":404,"data":"Data not found"}
        with patch.object(client.cf_session, 'get_session', return_value=({'cf_clearance':'test'},'UA')), patch.object(client.requests, 'get', return_value=response), patch.object(client.time, 'sleep'):
            result = client.fetch_schedules('GGL','04:00','07:00')
            self.assertEqual(result['data'], [])
            self.assertEqual(result['_http_status'], 404)
            self.assertEqual(result['_source_response'], response.json.return_value)
            with self.assertRaises(client.APIError): client.fetch_stations()
            response.json.return_value = {'message': 'Not Found'}
            with self.assertRaises(client.APIError): client.fetch_schedules('GGL')

    def test_retry_once_and_force(self):
        blocked = Mock(status_code=403, text="Just a moment")
        success = Mock(status_code=200, text='{}', headers={"content-type":"application/json"})
        success.json.return_value = {"data": []}
        with patch.object(client.cf_session, 'get_session', return_value=({'cf_clearance':'test'},'UA')) as session, patch.object(client.cf_session,'invalidate') as invalidate, patch.object(client.requests,'get',side_effect=[blocked,success]) as get, patch.object(client.time,'sleep'):
            self.assertEqual(client.fetch_stations(), {'data':[]})
            invalidate.assert_called_once()
            self.assertIn(unittest.mock.call(force=True), session.call_args_list)
            self.assertEqual(get.call_args.kwargs['headers']['User-Agent'], 'UA')
        with patch.object(client.cf_session,'get_session',return_value=({'cf_clearance':'test'},'UA')), patch.object(client.cf_session,'invalidate'), patch.object(client.requests,'get',return_value=blocked) as get, patch.object(client.time,'sleep'):
            with self.assertRaises(client.CloudflareError): client.fetch_stations()
            self.assertEqual(get.call_count, 2)

    def test_normalize_terminal_midnight_dedup_and_drop(self):
        stations=[{'sta_id':'A','sta_name':'Alpha','fg_enable':1},{'sta_id':'B','sta_name':'Beta','fg_enable':1},{'sta_id':'WIL0','fg_enable':'0'}]
        self.assertEqual(len(active(stations)),2)
        row={'train_id':'1','ka_name':'KRL','route_name':'Alpha-Beta','color':'#123456','dest':'Beta','time_est':'23:50:00','dest_time':'00:10:00'}
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)
            for name in ('A_1900_2200.json','A_2200_2400.json'):
                (cache/name).write_text(json.dumps({'data':[row]}))
            trips, report=normalize(cache,stations)
        self.assertEqual(trips[0]['stops'],[{'station_id':'A','t':85800},{'station_id':'B','t':87000}])
        self.assertEqual(report['unresolved_dest'],[])

    def test_osm_fuzzy_and_approximate_geometry(self):
        payload={'elements':[{'type':'node','id':1,'lat':-6.2,'lon':106.8,'tags':{'railway':'station','name':'Stasiun Alpha'}},{'type':'node','id':2,'lat':-6.21,'lon':106.81,'tags':{'railway':'halt','name':'Beta'}}]}
        stations=[{'sta_id':'A','sta_name':'ALPHA','fg_enable':1},{'sta_id':'B','sta_name':'BETA','fg_enable':1},{'sta_id':'Y','sta_name':'YOGYAKARTA','fg_enable':1}]
        trips=[{'route':'Alpha-Beta','color':'#123456','stops':[{'station_id':'A','t':0},{'station_id':'B','t':60}]}]
        points,lines,unmatched,methods=convert(payload,stations,trips)
        self.assertEqual(len(points),2)
        self.assertEqual(points[0]['mode'],'krl')
        self.assertEqual(len(unmatched),1)
        self.assertTrue(lines['features'][0]['properties']['approximate'])
        self.assertEqual(lines['features'][0]['geometry']['coordinates'],[[106.8,-6.2],[106.81,-6.21]])
        with self.assertRaises(RuntimeError): convert({'elements':[],'remark':'timeout'},stations,trips)

if __name__=='__main__': unittest.main()
