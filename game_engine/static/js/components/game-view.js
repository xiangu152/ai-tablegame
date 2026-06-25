/**
 * game-view.js — Main chat-app layout (~60 lines)
 * Composes: StatusBar, RoomSidebar, ChatArea, PlayerPanel
 */
const GameView = {
  template: `
  <div class="game-layout">
    <status-bar :title="title" :game="game" @back="$emit('back')" @export-chat="exportChat" @save-game="saveGame" />
    <div class="game-body">
      <room-sidebar :game="game" :selected="room" @select="onRoom" />
      <chat-area ref="chat" :game="game" :room="room" @new-message="onMsg" />
      <player-panel :game="game" @signal="onSignal" />
    </div>
  </div>`,
  props: { game: String, role: String },
  emits: ['back'],
  data() { return { room: '酒馆大厅' }; },
  computed: {
    title() { return (this.role === 'dm' ? '🎭 DM Panel' : '🎲 Player') + ' · ' + this.game; },
  },
  methods: {
    onRoom(name) {
      this.room = name;
    },
    onMsg(msg) {
      // Could trigger dice log refresh, sound effects, etc.
    },
    onSignal(target) {
      console.log('Signal sent to:', target);
    },
    exportChat() {
      const url = API + '/api/games/' + this.game + '/export?room=' + encodeURIComponent(this.room);
      const a = document.createElement('a');
      a.href = url; a.download = this.game + '_chat.zip';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    },
    async saveGame() {
      const r = await apiPost('/api/games/' + this.game + '/save', { label: 'auto' });
      alert(r && r.status === 'ok' ? '存档成功' : '存档失败');
    },
  },
};
