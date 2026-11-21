'use strict'

const mineflayer = require('mineflayer')

const [host, rawPort, username] = process.argv.slice(2)
const port = Number(rawPort)
if (host !== '127.0.0.1' || !Number.isInteger(port) || port < 1024 || port > 65535 || !/^[A-Za-z0-9_]{1,16}$/.test(username || '')) {
  process.stderr.write('Invalid local Minecraft companion arguments\n')
  process.exit(2)
}

function send (event) {
  process.stdout.write(JSON.stringify(event) + '\n')
}

let bot
let shuttingDown = false

try {
  bot = mineflayer.createBot({
    host,
    port,
    username,
    auth: 'offline',
    hideErrors: true,
    logErrors: false,
    viewDistance: 'short',
    checkTimeoutInterval: 20000
  })
} catch (error) {
  send({ type: 'status', status: 'error', error: String(error.message || error).slice(0, 300) })
  process.exit(1)
}

const readline = require('node:readline')
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })

bot.on('login', () => send({ type: 'status', status: 'connected', text: `Подключилась к локальному серверу как ${username}.` }))
bot.on('spawn', () => send({ type: 'status', status: 'online', text: 'Компаньон появился в мире.' }))
bot.on('kicked', (reason) => send({ type: 'status', status: 'error', error: String(reason).slice(0, 300) }))
bot.on('error', (error) => send({ type: 'status', status: 'error', error: String(error.message || error).slice(0, 300) }))
bot.on('end', () => {
  if (!shuttingDown) send({ type: 'status', status: 'disconnected', text: 'Соединение с сервером завершено.' })
  clearInterval(stateTimer)
  input.close()
  process.exitCode = 0
})
bot.on('chat', (name, message) => {
  if (name !== username) send({ type: 'chat', text: `${name}: ${message}` })
})
bot.on('whisper', (name, message) => send({ type: 'whisper', text: `${name} шепчет: ${message}` }))

const stateTimer = setInterval(() => {
  if (!bot.entity || !bot.entity.position) return
  const position = bot.entity.position
  const players = Object.values(bot.players)
    .filter((player) => player.username !== username && player.entity)
    .map((player) => player.username)
    .slice(0, 20)
  const nearby = Object.values(bot.entities)
    .filter((entity) => entity.type === 'mob' && entity.name && entity.position)
    .map((entity) => ({ name: entity.name, distance: Math.round(entity.position.distanceTo(position)) }))
    .filter((entity) => entity.distance <= 16)
    .sort((left, right) => left.distance - right.distance)
    .slice(0, 6)
  send({
    type: 'state',
    health: Math.round(Number(bot.health) || 0),
    food: Math.round(Number(bot.food) || 0),
    position: { x: Math.floor(position.x), y: Math.floor(position.y), z: Math.floor(position.z) },
    players,
    nearby
  })
}, 2000)

input.on('line', (line) => {
  let request
  try { request = JSON.parse(line) } catch { return }
  if (request.action === 'say' && typeof request.text === 'string' && request.text.length > 0 && request.text.length <= 200 && !request.text.startsWith('/')) {
    bot.chat(request.text)
  } else if (request.action === 'stop') {
    shuttingDown = true
    input.close()
    clearInterval(stateTimer)
    bot.quit('Quanta companion stopped')
  }
})

send({ type: 'status', status: 'connecting', text: `Подключаюсь к 127.0.0.1:${port}…` })
