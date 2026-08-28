/**
 * 可视化编辑器代理脚本（由 StaticResourceController 注入到预览 HTML 中）
 *
 * 预览页启用 CSP sandbox 后与主站不再同源，主站无法直接访问 iframe 的
 * contentDocument。本脚本在预览页内部完成元素悬浮/选中逻辑，
 * 通过 postMessage 与主站交换消息：
 *   父页面 -> 预览页: visual-editor:enable / visual-editor:disable / visual-editor:clear
 *   预览页 -> 父页面: visual-editor:selected（携带选中元素信息）
 */
;(function () {
  'use strict'

  var HOVER_STYLE_ID = '__visual_editor_style__'
  var HOVER_CLASS = '__ve_hover__'
  var SELECTED_CLASS = '__ve_selected__'

  var STYLE_CONTENT =
    '.' + HOVER_CLASS + ' {' +
    '  outline: 2px dashed #1677ff !important;' +
    '  outline-offset: -2px !important;' +
    '  cursor: pointer !important;' +
    '}' +
    '.' + SELECTED_CLASS + ' {' +
    '  outline: 3px solid #0958d9 !important;' +
    '  outline-offset: -3px !important;' +
    '  background-color: rgba(22, 119, 255, 0.08) !important;' +
    '}'

  var enabled = false
  var currentHover = null
  var currentSelected = null

  function buildSelector(el) {
    if (!el || el === document.documentElement) return 'html'
    var parts = []
    var cur = el
    var depth = 0
    while (cur && cur.nodeType === 1 && depth < 5) {
      var part = cur.tagName.toLowerCase()
      if (cur.id) {
        part += '#' + cur.id
        parts.unshift(part)
        break
      }
      var cls = (cur.getAttribute('class') || '').trim().split(/\s+/).filter(function (c) {
        return c && c !== HOVER_CLASS && c !== SELECTED_CLASS
      })
      if (cls.length) part += '.' + cls.slice(0, 2).join('.')
      var parent = cur.parentElement
      if (parent) {
        var sameTag = Array.prototype.filter.call(parent.children, function (c) {
          return c.tagName === cur.tagName
        })
        if (sameTag.length > 1) {
          part += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')'
        }
      }
      parts.unshift(part)
      cur = parent
      depth++
    }
    return parts.join(' > ')
  }

  function extractInfo(el) {
    var className = (el.getAttribute('class') || '')
      .split(/\s+/)
      .filter(function (c) {
        return c && c !== HOVER_CLASS && c !== SELECTED_CLASS
      })
      .join(' ')
    var text = (el.textContent || '').trim().slice(0, 100)
    return {
      tagName: el.tagName.toLowerCase(),
      id: el.id || undefined,
      className: className || undefined,
      textContent: text || undefined,
      selector: buildSelector(el),
    }
  }

  function onMouseOver(e) {
    var target = e.target
    if (!target || target === currentSelected) return
    if (currentHover && currentHover !== target) {
      currentHover.classList.remove(HOVER_CLASS)
    }
    currentHover = target
    if (target !== currentSelected) {
      target.classList.add(HOVER_CLASS)
    }
  }

  function onMouseOut(e) {
    var target = e.target
    if (target && target !== currentSelected) {
      target.classList.remove(HOVER_CLASS)
    }
  }

  function onClick(e) {
    e.preventDefault()
    e.stopPropagation()
    var target = e.target
    if (!target) return
    if (currentSelected && currentSelected !== target) {
      currentSelected.classList.remove(SELECTED_CLASS)
    }
    target.classList.remove(HOVER_CLASS)
    target.classList.add(SELECTED_CLASS)
    currentSelected = target
    window.parent.postMessage({ type: 'visual-editor:selected', payload: extractInfo(target) }, '*')
  }

  function injectStyle() {
    if (document.getElementById(HOVER_STYLE_ID)) return
    var style = document.createElement('style')
    style.id = HOVER_STYLE_ID
    style.textContent = STYLE_CONTENT
    document.head.appendChild(style)
  }

  function enable() {
    if (enabled) return
    enabled = true
    injectStyle()
    document.addEventListener('mouseover', onMouseOver, true)
    document.addEventListener('mouseout', onMouseOut, true)
    document.addEventListener('click', onClick, true)
  }

  function clearSelection() {
    if (currentSelected) {
      currentSelected.classList.remove(SELECTED_CLASS)
      currentSelected = null
    }
  }

  function disable() {
    if (!enabled) return
    enabled = false
    document.removeEventListener('mouseover', onMouseOver, true)
    document.removeEventListener('mouseout', onMouseOut, true)
    document.removeEventListener('click', onClick, true)
    if (currentHover) {
      currentHover.classList.remove(HOVER_CLASS)
      currentHover = null
    }
    clearSelection()
    var style = document.getElementById(HOVER_STYLE_ID)
    if (style) style.remove()
  }

  window.addEventListener('message', function (e) {
    var type = e.data && e.data.type
    if (type === 'visual-editor:enable') enable()
    else if (type === 'visual-editor:disable') disable()
    else if (type === 'visual-editor:clear') clearSelection()
  })
})()
