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

app = Flask(__name__)
app.config['SECRET_KEY'] = '*#Dolani#*'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tic_tac_toe.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app)
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    profile_pic = db.Column(db.String(255))
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

# Base64 encoded images for profile pictures
profile_pics = [
    "base64_encoded_image_1",
    "base64_encoded_image_2",
    "base64_encoded_image_3",
    "base64_encoded_image_4",
    "base64_encoded_image_5",
    "base64_encoded_image_6"
]

def cleanup_waiting_players():
    while True:
        current_time = time.time()
        for username, data in list(waiting_players.items()):
            if current_time - data['timestamp'] > 300:  # 5 minutes timeout
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
    new_user.profile_pic = random.choice(profile_pics)
    
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
        return jsonify({"message": "Logged in successfully"}), 200
    
    return jsonify({"message": "Invalid username or password"}), 401

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    if 'username' in session:
        username = session['username']
        user = User.query.filter_by(username=username).first()
        if user and username not in waiting_players:
            waiting_players[username] = {
                'session_id': request.sid,
                'timestamp': time.time()
            }
            emit('queue_joined', {'message': f'Rejoined queue as {username}'}, room=request.sid)
            socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)
    emit('connection_established', {'message': 'Connected to server'})

@socketio.on('join_queue')
def join_queue(data):
    username = session.get('username')
    if username and username not in waiting_players:
        waiting_players[username] = {
            'session_id': request.sid,
            'timestamp': time.time()
        }
        emit('queue_joined', {'message': f'Joined queue as {username}'}, room=request.sid)
        socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)
        check_for_game()

def check_for_game():
    if len(waiting_players) >= 2:
        players = list(waiting_players.keys())
        random.shuffle(players)  # Randomize player selection
        player1, player2 = players[:2]
        game_id = str(uuid.uuid4())
        active_games[game_id] = {
            'players': [player1, player2],
            'board': ['' for _ in range(9)],
            'current_turn': random.choice([player1, player2])
        }
        for player in [player1, player2]:
            join_room(game_id, sid=waiting_players[player]['session_id'])
            opponent = player2 if player == player1 else player1
            socketio.emit('game_start', {
                'game_id': game_id,
                'opponent': opponent,
                'your_turn': player == active_games[game_id]['current_turn']
            }, room=waiting_players[player]['session_id'])
            del waiting_players[player]
        socketio.emit('queue_updated', {'waiting_players': list(waiting_players.keys())}, room=None)

@socketio.on('make_move')
def make_move(data):
    game_id = data['game_id']
    player = session.get('username')
    position = data['position']
    
    if game_id in active_games and player == active_games[game_id]['current_turn']:
        game = active_games[game_id]
        if game['board'][position] == '':
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
    print('Client disconnected')
    username = session.get('username')
    if username in waiting_players:
        del waiting_players[username]
        socketio.emit('queue_left', {'message': f'{username} left the queue'}, room=None)
    for game_id, game in list(active_games.items()):
        if username in game['players']:
            opponent = game['players'][0] if game['players'][1] == username else game['players'][1]
            socketio.emit('game_over', {'winner': opponent, 'reason': 'disconnect'}, room=game_id)
            update_user_stats(opponent, won=True)
            update_user_stats(username, won=False)
            del active_games[game_id]

if __name__ == '__main__':
    socketio.run(app, debug=True)