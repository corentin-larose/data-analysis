# this example uses requests
import requests
import json

params = {
  'models': 'nudity-2.1,weapon,alcohol,recreational_drug,medical,offensive-2.0,face-age,gore-2.0,violence,self-harm',
  'api_user': '{api_user}',
  'api_secret': '{api_secret}'
}
files = {'media': open('/full/path/to/image.jpg', 'rb')}
r = requests.post('https://api.sightengine.com/1.0/check.json', files=files, data=params)

output = json.loads(r.text)
