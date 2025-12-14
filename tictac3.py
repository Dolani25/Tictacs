
import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import random
from collections import defaultdict
import uuid
import time
import threading
import base64
from flask_session import Session
import redis
import os
import json

def get_random_profile_picture():
    # Load the Base64 images from the external JSON file
    with open("profile_img.json", "r") as file:
        base64_images = json.load(file)
    
    # Return a random image from the list
    return random.choice(base64_images)


# Get the Database URL from environment variable
DATABASE_URL = "postgres://postgres.nqyxyetdnabdfhxfxsms:QjUdNAGhdpcZrTlG@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"


# Ensure the URL starts with 'postgresql://' instead of 'postgres://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.config['SECRET_KEY'] = '*#Dolani#*'
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

allowed_origins_env = os.environ.get('CORS_ALLOWED_ORIGINS', '')
allowed_origins = [o.strip() for o in allowed_origins_env.split(',') if o.strip()]
cors_origins = allowed_origins or '*'

# Redis configuration
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = redis.from_url('redis://default:onbRkMMdpcOdIyuCgbW4mOCMkhZ5bnqT@redis-13527.c44.us-east-1-2.ec2.redns.redis-cloud.com:13527')

app.config['SESSION_COOKIE_HTTPONLY'] = True

app.config['SESSION_COOKIE_SECURE'] = True

app.config['SESSION_COOKIE_SAMESITE'] = 'None'

Session(app)



# Configure CORS
CORS(app, resources={r"/*": {"origins": cors_origins}}, supports_credentials=True)


db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins=cors_origins, cors_credentials=True, manage_session=False)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(400))
    profile_pic = db.Column(db.Text)
    cumulative_score = db.Column(db.Integer, default=0)
    ranking = db.Column(db.Integer)
    total_games_played = db.Column(db.Integer, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Create tables
with app.app_context():
    db.create_all()

# In-memory storage for active games and waiting players
waiting_players = {}
active_games = {}
sid_to_username = {}



def cleanup_waiting_players():
    while True:
        current_time = time.time()
        for username, data in list(waiting_players.items()):
            if current_time - data['timestamp'] > 60:  # 60 seconds timeout
                del waiting_players[username]
                socketio.emit('queue_left', {'message': f'{username} removed from queue due to inactivity'}, room=None)
        time.sleep(60)  # Check every minute

# Start the cleanup thread
cleanup_thread = threading.Thread(target=cleanup_waiting_players)
cleanup_thread.daemon = True
cleanup_thread.start()

@app.route('/register', methods=['POST'])
def register():
    username = request.json.get('username')
    password = request.json.get('password')
    
    if not username or not password:
        return jsonify({"message": "Username and password are required"}), 400
    
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        return jsonify({"message": "Username already taken"}), 400
    
    new_user = User(username=username)
    new_user.set_password(password)
    #Base64 encoded images for profile pictures
    new_user.profile_pic = get_random_profile_picture()
    
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({"message": "User registered successfully"}), 200

@app.route('/login', methods=['POST'])
def login():
    username = request.json.get('username')
    password = request.json.get('password')
    
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        session['user_id'] = user.id
        session['username'] = user.username
        session.modified = True
        return jsonify({"message": "Logged in successfully", "user_id": user.id}), 200
    
    return jsonify({"message": "Invalid username or password"}), 401

@socketio.on('connect')
def handle_connect():
    print(f'Client connected sid={request.sid}')
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user:
            sid_to_username[request.sid] = user.username
        if user and user.username not in waiting_players:
            waiting_players[user.username] = {
                'session_id': request.sid,
                'timestamp': time.time()
            }
            emit('queue_joined', {'message': f'Rejoined queue as {user.username}'}, room=request.sid)
            socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)
    emit('connection_established', {'message': 'Connected to server'})


@socketio.on('authenticate')
def authenticate(data):
    user_id = None
    username = None
    if isinstance(data, dict):
        user_id = data.get('user_id')
        username = data.get('username')

    if user_id and not username:
        user = User.query.get(user_id)
        if user:
            username = user.username

    if not username:
        print(f"Auth failed sid={request.sid} data={data}")
        emit('auth_failed', {'message': 'Missing username'}, room=request.sid)
        return

    sid_to_username[request.sid] = username
    try:
        session['username'] = username
        session.modified = True
    except Exception:
        pass

    print(f"Authenticated sid={request.sid} username={username}")
    emit('authenticated', {'message': 'Authenticated', 'username': username}, room=request.sid)

"""
@socketio.on('join_queue')
def join_queue(data):
    user_id = session.get('user_id')
    if user_id:
        user = User.query.get(user_id)
        if user and user.username not in waiting_players:
            waiting_players[user.username] = {
                'session_id': request.sid,
                'timestamp': time.time()
            }
            emit('queue_joined', {'message': f'Joined queue as {user.username}'}, room=request.sid)
            socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)
            check_for_game()
"""

@socketio.on('join_queue')
def join_queue(data):
    username = None
    if isinstance(data, dict):
        username = data.get('username')
    if not username:
        username = sid_to_username.get(request.sid) or session.get('username')

    if not username:
        print(f"Queue join failed sid={request.sid} data={data}")
        emit('queue_join_failed', {'message': 'Not authenticated'}, room=request.sid)
        return

    sid_to_username[request.sid] = username

    if username not in waiting_players:
        waiting_players[username] = {
            'session_id': request.sid,
            'timestamp': time.time()
        }
        print(f"Queue joined username={username} sid={request.sid} waiting={len(waiting_players)}")
        emit('queue_joined', {'message': f'Joined queue as {username}'}, room=request.sid)
        socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)
        check_for_game()
    else:
        waiting_players[username]['session_id'] = request.sid
        waiting_players[username]['timestamp'] = time.time()
        print(f"Queue re-joined username={username} sid={request.sid} waiting={len(waiting_players)}")
        emit('queue_joined', {'message': f'Already in queue as {username}'}, room=request.sid)



def check_for_game():
    if len(waiting_players) >= 2:
        players = list(waiting_players.keys())
        random.shuffle(players)  # Randomize player selection
        player1_username, player2_username = players[:2]
        print(f"Matchmaking pair {player1_username} vs {player2_username}")

        # Fetch user objects to access profile pictures
        player1 = User.query.filter_by(username=player1_username).first()
        player2 = User.query.filter_by(username=player2_username).first()

        if not player1 or not player2:
            print("Error: Could not retrieve user details for game start.")
            return

        game_id = str(uuid.uuid4())
        active_games[game_id] = {
            'players': [player1_username, player2_username],
            'board': ['' for _ in range(9)],
            'current_turn': random.choice([player1_username, player2_username])
        }
        print(f"Game created id={game_id} turn={active_games[game_id]['current_turn']}")
        for player_username, user_obj in zip([player1_username, player2_username], [player1, player2]):
            join_room(game_id, sid=waiting_players[player_username]['session_id'])
            opponent_username = player2_username if player_username == player1_username else player1_username
            opponent_obj = player2 if player_username == player1_username else player1

            socketio.emit('game_start', {
                'game_id': game_id,
                'opponent': opponent_username,
                'opponent_profile_pic': opponent_obj.profile_pic,  # Send opponent's profile picture
                'your_turn': player_username == active_games[game_id]['current_turn']
            }, room=waiting_players[player_username]['session_id'])
            del waiting_players[player_username]
        socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)


@socketio.on('make_move')
def make_move(data):
    game_id = data['game_id']
    player = sid_to_username.get(request.sid) or session.get('username')
    if not player and isinstance(data, dict):
        player = data.get('player')
    position = data['position']

    if not player:
        print(f"Move failed unauth sid={request.sid} data={data}")
        emit('move_failed', {'message': 'Not authenticated'}, room=request.sid)
        return

    if game_id in active_games and player == active_games[game_id]['current_turn']:
        game = active_games[game_id]
        if game['board'][position] == '':
            print(f"Move accepted game={game_id} player={player} pos={position}")
            game['board'][position] = 'X' if player == game['players'][0] else 'O'
            game['current_turn'] = game['players'][1] if player == game['players'][0] else game['players'][0]
            
            emit('move_made', {'position': position, 'player': player}, room=game_id)
            
            winner, winning_combination = check_winner(game['board'])
            if winner:
                emit('game_over', {'winner': player, 'winningCombination': winning_combination}, room=game_id)
                update_user_stats(player, won=True)
                update_user_stats(game['players'][0] if player == game['players'][1] else game['players'][1], won=False)
                del active_games[game_id]
            elif '' not in game['board']:
                emit('game_over', {'winner': 'draw'}, room=game_id)
                update_user_stats(game['players'][0], draw=True)
                update_user_stats(game['players'][1], draw=True)
                del active_games[game_id]
            else:
                emit('next_turn', {'player': game['current_turn']}, room=game_id)
        else:
            print(f"Move rejected taken game={game_id} player={player} pos={position}")
            emit('move_failed', {'message': 'Cell already taken'}, room=request.sid)
    else:
        print(f"Move rejected invalid/turn game={game_id} player={player} sid={request.sid}")
        emit('move_failed', {'message': 'Invalid game or not your turn'}, room=request.sid)

def check_winner(board):
    winning_combinations = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],  # Rows
        [0, 3, 6], [1, 4, 7], [2, 5, 8],  # Columns
        [0, 4, 8], [2, 4, 6]  # Diagonals
    ]
    for combo in winning_combinations:
        if board[combo[0]] == board[combo[1]] == board[combo[2]] != '':
            return True, combo
    return False, None

def update_user_stats(username, won=False, draw=False):
    user = User.query.filter_by(username=username).first()
    if user:
        user.total_games_played += 1
        if won:
            user.cumulative_score += 3
        elif draw:
            user.cumulative_score += 1
        db.session.commit()
    update_rankings()

def update_rankings():
    users = User.query.order_by(User.cumulative_score.desc()).all()
    for i, user in enumerate(users, 1):
        user.ranking = i
    db.session.commit()

@app.route('/leaderboard', methods=['GET'])
def get_leaderboard():
    users = User.query.order_by(User.cumulative_score.desc()).limit(10).all()
    leaderboard = [{'username': user.username, 'score': user.cumulative_score, 'ranking': user.ranking} for user in users]
    return jsonify(leaderboard)

@app.route('/profile', methods=['GET'])
def get_profile():
    print("session data : " , session)
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"message": "Not logged in"}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "User not found"}), 404
    
    return jsonify({
        "username": user.username,
        "profile_pic": user.profile_pic,
        "cumulative_score": user.cumulative_score,
        "ranking": user.ranking,
        "total_games_played": user.total_games_played
    })

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected sid={request.sid}')
    username = sid_to_username.pop(request.sid, None) or session.get('username')
    if username in waiting_players:
        del waiting_players[username]
        print(f"Queue left username={username}")
        socketio.emit('queue_left', {'message': f'{username} left the queue'}, room=None)
    for game_id, game in list(active_games.items()):
        if username in game['players']:
            opponent = game['players'][0] if game['players'][1] == username else game['players'][1]
            print(f"Game over disconnect game={game_id} leaver={username} winner={opponent}")
            socketio.emit('game_over', {'winner': opponent, 'reason': 'disconnect'}, room=game_id)
            update_user_stats(opponent, won=True)
            update_user_stats(username, won=False)
            del active_games[game_id]

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8000)

