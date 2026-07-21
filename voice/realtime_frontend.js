// ── Voice (实时语音: MediaRecorder 流式直传) ──
var voiceWs = null;
var voiceActive = false;
var mediaRecorder = null;
var audioStream = null;

async function toggleVoice() {
  var btn = document.getElementById('voice-btn');
  var indicator = document.getElementById('voice-indicator');
  var vt = document.getElementById('voice-text');

  if (voiceActive) {
    stopVoice();
    return;
  }

  try {
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    alert('麦克风访问被拒绝: ' + e.message);
    return;
  }

  var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  voiceWs = new WebSocket(proto + '//' + window.location.host + '/ws_voice');
  voiceActive = true;

  voiceWs.onopen = function() {
    var mimeOpts = { mimeType: 'audio/webm;codecs=opus' };
    try { mediaRecorder = new MediaRecorder(audioStream, mimeOpts); }
    catch (e) { mediaRecorder = new MediaRecorder(audioStream); }
    mediaRecorder.ondataavailable = function(e) {
      if (e.data.size > 0 && voiceWs && voiceWs.readyState === WebSocket.OPEN) {
        var reader = new FileReader();
        reader.onloadend = function() {
          var b64 = reader.result.split(',')[1];
          voiceWs.send(JSON.stringify({ type: 'audio_chunk', data: b64 }));
        };
        reader.readAsDataURL(e.data);
      }
    };
    mediaRecorder.onstop = function() {
      if (voiceWs && voiceWs.readyState === WebSocket.OPEN) {
        voiceWs.send(JSON.stringify({ type: 'audio_end' }));
      }
    };
    mediaRecorder.start(); // 无 timeslice：停止时一次性发完整音频
    voiceWs.send(JSON.stringify({ type: 'audio_start', mime: 'audio/webm;codecs=opus' }));
  };

  voiceWs.onmessage = function(e) {
    var msg = JSON.parse(e.data);
    if (msg.type === 'status') {
      indicator.classList.add('active');
      if (msg.state === 'executing' && msg.tool) {
        vt.innerText = '执行: ' + msg.tool;
      } else {
        vt.innerText = msg.state === 'listening' ? '正在听...' :
                       msg.state === 'thinking' ? '思考中...' :
                       msg.state === 'speaking' ? '回复中...' : msg.state;
      }
    } else if (msg.type === 'transcript') {
      vt.innerText = msg.text || '';
    } else if (msg.type === 'text') {
      if (!isProcessing) { setProcessing(true); tsShow(); }
      appendMessage('assistant', msg.content, true);
    } else if (msg.type === 'audio' && msg.data) {
      playAudio(msg.data);
    } else if (msg.type === 'done') {
      tsHide(); setProcessing(false);
      indicator.classList.remove('active'); vt.innerText = '';
    } else if (msg.type === 'error') {
      addCard('system', '语音错误: ' + msg.message);
    }
  };
  voiceWs.onclose = function() { stopVoice(); };
  voiceWs.onerror = function() { stopVoice(); };

  btn.classList.add('recording');
  btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic-fill"/></svg>';
  indicator.classList.add('active');
  vt.innerText = '正在听...';
  indicator.style.display = '';
}

function stopVoice() {
  var btn = document.getElementById('voice-btn');
  var indicator = document.getElementById('voice-indicator');
  var vt = document.getElementById('voice-text');
  voiceActive = false;
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop(); } catch(e) {}
  }
  if (audioStream) {
    audioStream.getTracks().forEach(function(t) { t.stop(); });
    audioStream = null;
  }
  if (voiceWs) {
    try { voiceWs.close(); } catch(e) {}
    voiceWs = null;
  }
  mediaRecorder = null;
  btn.classList.remove('recording');
  btn.innerHTML = '<svg width="18" height="18"><use href="#i-mic"/></svg>';
  indicator.classList.remove('active');
  vt.innerText = '';
}

