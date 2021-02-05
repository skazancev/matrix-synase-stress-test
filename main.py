from functools import cached_property
from typing import Optional, Tuple

from faker import Faker
from locust import task, between
from locust.contrib.fasthttp import FastHttpUser


class MatrixUserMixin:
    access_token: Optional[str] = None

    def _send_request(self, matrix_method, **kwargs):
        path = f'/_matrix/client/r0/{matrix_method}'
        if self.access_token:
            path += f'?access_token={self.access_token}'

        kwargs.setdefault('name', matrix_method)
        return self.client.request(path=path, **kwargs)

    def send_get(self, matrix_method, **kwargs):
        return self._send_request(matrix_method, method='GET', **kwargs)

    def send_post(self, matrix_method, **kwargs):
        return self._send_request(matrix_method, method='POST', **kwargs)

    def login(self, credentials) -> Tuple[dict, int]:
        response = self.send_post('login', json=credentials)
        response_json = response.json()
        if response.status_code == 200:
            self.access_token = response_json['access_token']
            print('Logged in:', credentials['user'])
        else:
            print('Login error:', response_json, credentials)

        if response.status_code == 403:
            return self.signup(credentials)

        return response_json, response.status_code

    def signup(self, credentials) -> Tuple[dict, int]:
        response = self.send_post('register', json={
            'username': credentials['user'],
            'password': credentials['password'],
            'auth': {
                'type': 'm.login.dummy'
            }
        })
        self.access_token = response.json()['access_token']
        print('REGISTERED:', response.json())

        return response.json(), response.status_code


class HostUser(MatrixUserMixin):

    credentials = {
        'password': "password",
        'type': "m.login.password",
        'user': "host_user",
    }

    def set_client(self, client):
        if not hasattr(self, 'client'):
            setattr(self, 'client', client)
        return getattr(self, 'client')

    def _send_request(self, matrix_method, **kwargs):
        if not self.logged_in and matrix_method not in ['register', 'login']:
            self.login(self.credentials)
        return super()._send_request(matrix_method, **kwargs)

    @property
    def logged_in(self):
        return self.access_token is not None

    @cached_property
    def room_id(self):
        sync_response = self.send_get('sync').json()
        if room := list(sync_response['rooms']['join'].keys()):
            return room[0]

        response = self.send_post('createRoom', json={'room_alias_name': 'stress-testing-room'})
        return response.json()['room_id']

    def invite(self, user_id):
        response = self.send_post(f'rooms/{self.room_id}/invite', json={'user_id': user_id}, name='invite')
        print('INVITED:', response.json())


host_user = HostUser()


class RoomUser(MatrixUserMixin, FastHttpUser):
    last_user_id: int = 0
    wait_time = between(1, 2.5)
    faker = Faker()
    added_to_room: bool = False

    @task()
    def send_messages(self):
        for i in range(5):
            response = self.send_post(
                f'rooms/{host_user.room_id}/send/m.room.message',
                json={'msgtype': 'm.text', 'body': self.faker.text()[:100]},
                name='SendMessage'
            )
            if response.status_code == 200:
                print('Message sent!')
            else:
                print('Message error:', response.json())

    def get_credentials(self):
        RoomUser.last_user_id += 1
        return {
            'password': 'mysecretpassword',
            'user': f'stress_testing{RoomUser.last_user_id}',
            'type': "m.login.password",
        }

    def on_start(self):
        credentials = self.get_credentials()
        response, code = self.login(credentials)

        sync_response = self.send_get('sync').json()
        host_user.set_client(client=self.client)
        if not sync_response.get('rooms'):
            print('SYNC ERROR', sync_response)
        if host_user.room_id not in sync_response['rooms']['join'].keys():
            host_user.invite(response['user_id'])
            self.send_post(f'rooms/{host_user.room_id}/join', name='join')
