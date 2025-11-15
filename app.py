from flask import Flask, render_template
import os
from datetime import datetime
import requests

app = Flask(__name__)

class Config:
    BITLY_TOKEN = os.environ.get('BITLY_TOKEN', '')
    CUTTLY_API = os.environ.get('CUTTLY_API', '')
    GPLINKS_API = os.environ.get('GPLINKS_API', '')

config = Config()

def check_service_status():
    """Check status of each service"""
    status = {
        'bitly': bool(config.BITLY_TOKEN),
        'cuttly': bool(config.CUTTLY_API),
        'gplinks': bool(config.GPLINKS_API)
    }
    
    # Count connected services (TinyURL always counts as connected)
    connected_count = sum(status.values()) + 1  # +1 for TinyURL
    
    return status, connected_count

@app.route('/')
def index():
    """Main status page"""
    services_status, connected_count = check_service_status()
    
    context = {
        'bitly_status': services_status['bitly'],
        'cuttly_status': services_status['cuttly'],
        'gplinks_status': services_status['gplinks'],
        'bitly_key': config.BITLY_TOKEN,
        'cuttly_key': config.CUTTLY_API,
        'gplinks_key': config.GPLINKS_API,
        'connected_services': connected_count,
        'current_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return render_template('index.html', **context)

@app.route('/api/status')
def api_status():
    """JSON API endpoint for status"""
    services_status, connected_count = check_service_status()
    
    return {
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'services': {
            'bitly': {
                'connected': services_status['bitly'],
                'has_key': bool(config.BITLY_TOKEN)
            },
            'tinyurl': {
                'connected': True,
                'has_key': False
            },
            'cuttly': {
                'connected': services_status['cuttly'],
                'has_key': bool(config.CUTTLY_API)
            },
            'gplinks': {
                'connected': services_status['gplinks'],
                'has_key': bool(config.GPLINKS_API)
            }
        },
        'summary': {
            'total_services': 4,
            'connected_services': connected_count
        }
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
