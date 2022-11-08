import json

def save_dic(json_name, save_name):
    di = json.dumps(json_name, sort_keys=False, indent=4, separators=(',', ':'))
    with open('./rel/' + str(save_name) + '.json', 'w') as f:
        f.write(di)


def save_dic_tensor(json_name, save_name):
    for key in json_name.keys():
        for _ in json_name[key].keys():
            json_name[key][_] = json_name[key][_].tolist()
    di = json.dumps(json_name, sort_keys=False, indent=4, separators=(',', ':'))
    with open('./rel/' + str(save_name) + '.json', 'w') as f:
        f.write(di)


def save_tensor(json_name, save_name):
    for key in json_name.keys():
        json_name[key] = json_name[key].tolist()
    di = json.dumps(json_name, sort_keys=False, indent=4, separators=(',', ':'))
    with open('./rel/' + str(save_name) + '.json', 'w') as f:
        f.write(di)