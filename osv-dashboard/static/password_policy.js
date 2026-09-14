/* Shared password-policy meter.  This is advisory only; Flask validates again. */
(function (global) {
  'use strict';
  var MINIMUM = 15;
  var MAXIMUM = 4096;
  var common = /^(password|passw0rd|qwerty|123456|12345678|123456789|1234567890|admin|administrator|letmein|welcome|changeme|default|root|user|пароль|админ|йцукен)$/i;
  function commonPassword(value) {
    var candidate = comparable(value);
    if (common.test(candidate)) return true;
    var match = candidate.match(/^(password|passw0rd|qwerty|admin|administrator|letmein|welcome|changeme|default|root|user|пароль|админ|йцукен)(\d{1,16})$/i);
    return !!match;
  }
  function comparable(value) {
    return value.toLocaleLowerCase();
  }
  function derived(password, username) {
    var candidate = comparable(password);
    var name = comparable(username || '').replace(/[^0-9a-zа-яё]/gi, '');
    if (!name) return false;
    if (candidate === name) return true;
    var suffixes = ['123', '1234', '!', '!!', '2024', '2025', '2026',
      'password', 'пароль', 'admin', 'админ'];
    return suffixes.some(function (part) {
      return candidate === name + part || candidate === part + name;
    }) || (name.length >= 5 &&
      ((candidate.indexOf(name) === 0 && candidate.length - name.length <= 4) ||
       (candidate.slice(-name.length) === name && candidate.length - name.length <= 4)));
  }
  function init(options) {
    var password = options.password, repeat = options.repeat;
    if (!password || !repeat) return;
    function update() {
      var value = password.value; // intentionally do not trim or normalize
      var length = Array.from(value).length;
      var login = typeof options.username === 'function'
        ? options.username() : options.username;
      var okay = length >= MINIMUM && length <= MAXIMUM &&
         !commonPassword(value) &&
        !derived(value, login) && value === repeat.value;
      if (options.meter) {
        options.meter.style.width = Math.min(100, length / MINIMUM * 100) + '%';
        options.meter.style.background = okay ? '#2e7d4f' : '#c98a12';
      }
      if (options.message) {
        options.message.textContent = length < MINIMUM
          ? 'Минимум 15 символов Unicode. Пробелы разрешены, обязательных классов символов нет.'
          : (length > MAXIMUM ? 'Пароль слишком длинный (максимум 4096 символов Unicode).'
           : (commonPassword(value) || derived(value, login)
            ? 'Пароль слишком распространённый или связан с логином.'
            : (value !== repeat.value
              ? 'Повторите новый пароль без изменений.'
              : 'Пароль соответствует базовой политике.')));
        options.message.classList.toggle('valid', okay);
      }
      if (options.submit) options.submit.disabled = !okay;
    }
    password.addEventListener('input', update);
    repeat.addEventListener('input', update);
    update();
  }
  global.PasswordPolicyMeter = { init: init };
}(window));