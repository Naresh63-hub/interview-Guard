import os
import unittest
import time
import hmac
import hashlib
from flask import session
from app import app, socketio
import state

class IntervueTestCase(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-12345'
        app.secret_key = 'test-secret-key-12345'
        self.client = app.test_client()
        
        # Clear mock rooms
        state.meeting_rooms.clear()

    def tearDown(self):
        state.meeting_rooms.clear()

    def test_health_route(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        json_data = response.get_json()
        self.assertTrue(json_data['ok'])
        self.assertEqual(json_data['mode'], 'browser-frame-analysis')

    def test_security_headers_are_set(self):
        response = self.client.get('/health')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')

    def test_create_room_unauthorized(self):
        # Test without password
        response = self.client.post('/create-room', json={
            "hostName": "TestHost",
            "title": "Test Room"
        })
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())
        
        # Test with wrong password
        response = self.client.post('/create-room', json={
            "hostName": "TestHost",
            "password": "wrong_password",
            "title": "Test Room"
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], "Invalid host credentials")

    def test_create_room_authorized(self):
        # Test with correct default password
        response = self.client.post('/create-room', json={
            "hostName": "TestHost",
            "password": "admin123",
            "title": "Test Room"
        })
        self.assertEqual(response.status_code, 201)
        json_data = response.get_json()
        self.assertIn('meetingId', json_data)
        self.assertIn('link', json_data)
        
        # Check signed link contents
        link = json_data['link']
        self.assertTrue(link.startswith("/meet/"))
        self.assertIn("sig=", link)
        self.assertIn("expires=", link)
        
        # Room should be created in state
        meeting_id = json_data['meetingId']
        self.assertIn(meeting_id, state.meeting_rooms)
        self.assertEqual(state.meeting_rooms[meeting_id]['host'], "TestHost")

    def test_meet_route_validation(self):
        # Create a mock room
        meeting_id = "MOCKROOM"
        state.meeting_rooms[meeting_id] = {
            "id": meeting_id,
            "title": "Mock",
            "host": "Host",
            "createdAt": time.time(),
            "participants": [],
            "status": "waiting"
        }
        
        # Case 1: Missing signature and expires
        response = self.client.get(f'/meet/{meeting_id}')
        self.assertEqual(response.status_code, 403)
        self.assertIn("Missing signature", response.get_data(as_text=True))
        
        # Case 2: Expired timestamp
        expires = int(time.time()) - 10  # 10s in the past
        sig_payload = f"{meeting_id}-{expires}"
        sig = hmac.new(app.secret_key.encode(), sig_payload.encode(), hashlib.sha256).hexdigest()
        response = self.client.get(f'/meet/{meeting_id}?sig={sig}&expires={expires}')
        self.assertEqual(response.status_code, 403)
        self.assertIn("expired", response.get_data(as_text=True))

        # Case 3: Invalid signature
        expires = int(time.time()) + 1000
        response = self.client.get(f'/meet/{meeting_id}?sig=fakesig123&expires={expires}')
        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid signature", response.get_data(as_text=True))

        # Case 4: Valid signature and expires
        expires = int(time.time()) + 3600
        sig_payload = f"{meeting_id}-{expires}"
        sig = hmac.new(app.secret_key.encode(), sig_payload.encode(), hashlib.sha256).hexdigest()
        
        response = self.client.get(f'/meet/{meeting_id}?sig={sig}&expires={expires}')
        self.assertEqual(response.status_code, 302)  # Should redirect to login/candidate
        self.assertTrue(response.location.endswith(f"/login/candidate?meeting_id={meeting_id}"))

    def test_candidate_access_without_session(self):
        meeting_id = "MOCKROOM"
        # Accessing dashboard directly should return 403
        response = self.client.get(f'/candidate_dashboard/{meeting_id}')
        self.assertEqual(response.status_code, 403)

        response = self.client.get(f'/login/candidate?meeting_id={meeting_id}')
        self.assertEqual(response.status_code, 403)

    def test_analysis_endpoints_require_candidate_session(self):
        response = self.client.post('/gaze-frame', json={"image": "ignored"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Candidate session required", response.get_json()["error"])

        response = self.client.post('/analyze-audio', json={"audio": "ignored"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Candidate session required", response.get_json()["error"])

        response = self.client.post('/liveness-frame', json={"image": "ignored"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Candidate session required", response.get_json()["error"])

        response = self.client.post('/analyze_network', json={"ip": "127.0.0.1"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Candidate session required", response.get_json()["error"])

    def test_candidate_session_reaches_gaze_validation(self):
        meeting_id = "MOCKROOM"
        with self.client.session_transaction() as sess:
            sess["candidate_verified_meeting"] = meeting_id

        response = self.client.post('/gaze-frame', json={
            "meetingId": meeting_id,
            "image": ""
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Missing image")

    def test_candidate_session_reaches_liveness_validation(self):
        meeting_id = "MOCKROOM"
        with self.client.session_transaction() as sess:
            sess["candidate_verified_meeting"] = meeting_id

        response = self.client.post('/liveness-frame', json={
            "meetingId": meeting_id,
            "image": ""
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Missing image")

    def test_socket_unauthorized_join(self):
        meeting_id = "MOCKROOM"
        state.meeting_rooms[meeting_id] = {
            "id": meeting_id,
            "title": "Mock",
            "host": "Host",
            "createdAt": time.time(),
            "participants": [],
            "status": "waiting"
        }
        
        # Test candidate join via SocketIO client
        sio_client = socketio.test_client(app)
        
        # Try joining candidate without session approval
        sio_client.emit("join_meeting", {
            "meetingId": meeting_id,
            "userName": "Alice",
            "role": "candidate"
        })
        
        # Client should receive an error event
        received = sio_client.get_received()
        error_events = [event for event in received if event['name'] == 'error']
        self.assertTrue(len(error_events) > 0)
        self.assertIn("Unauthorized: Candidate session invalid", error_events[0]['args'][0]['message'])

    def test_socket_proctoring_events_only_relay_from_candidate(self):
        meeting_id = "MOCKROOM"
        state.meeting_rooms[meeting_id] = {
            "id": meeting_id,
            "title": "Mock",
            "host": "Host",
            "createdAt": time.time(),
            "participants": [],
            "status": "waiting"
        }

        host_http = app.test_client()
        with host_http.session_transaction() as sess:
            sess["host_authenticated"] = True
        host_socket = socketio.test_client(app, flask_test_client=host_http)
        host_socket.emit("join_meeting", {
            "meetingId": meeting_id,
            "userName": "Host",
            "role": "interviewer"
        })
        host_socket.get_received()

        candidate_http = app.test_client()
        with candidate_http.session_transaction() as sess:
            sess["candidate_verified_meeting"] = meeting_id
        candidate_socket = socketio.test_client(app, flask_test_client=candidate_http)
        candidate_socket.emit("join_meeting", {
            "meetingId": meeting_id,
            "userName": "Alice",
            "role": "candidate"
        })
        host_socket.get_received()
        candidate_socket.get_received()

        host_socket.emit("gaze_update", {
            "meetingId": meeting_id,
            "gazeData": {"direction": "HOST_SHOULD_NOT_RELAY"}
        })
        candidate_received = candidate_socket.get_received()
        self.assertFalse(any(event["name"] == "gaze_update" for event in candidate_received))

        candidate_socket.emit("gaze_update", {
            "meetingId": meeting_id,
            "gazeData": {"direction": "CENTER"}
        })
        host_received = host_socket.get_received()
        gaze_events = [event for event in host_received if event["name"] == "gaze_update"]
        self.assertEqual(len(gaze_events), 1)
        self.assertEqual(gaze_events[0]["args"][0]["gazeData"]["direction"], "CENTER")

if __name__ == '__main__':
    unittest.main()
