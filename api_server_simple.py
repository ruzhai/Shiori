from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/api/test', methods=['GET'])
def test():
    return jsonify({'status': 'ok', 'message': 'Flask is working'})

@app.route('/api/init', methods=['POST'])
def init_agent():
    data = request.json
    print(f"Received: {data}")
    return jsonify({'status': 'ok', 'received': data})

if __name__ == '__main__':
    app.run(port=5000, debug=True, threaded=True)
