import { test, expect } from '@playwright/test'

test('topology editor shell loads as 3D editor without legacy iframe', async ({ page }) => {
  page.on('pageerror', (error) => {
    console.log('PAGEERROR:', error.message)
  })

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.log('CONSOLE_ERROR:', msg.text())
    }
  })

  await page.goto('http://127.0.0.1:5173/topology')

  // 标题与工具栏
  await expect(page.getByText('拓扑编辑器').first()).toBeVisible()
  await expect(page.getByLabel('拓扑名称')).toBeVisible()
  await expect(page.getByRole('button', { name: '保存拓扑' })).toBeVisible()

  // 不再嵌入旧 iframe
  await expect(page.locator('iframe')).toHaveCount(0)

  // 调色板分组
  await expect(page.getByText('区域（拉矩形）')).toBeVisible()

  // 3D 场景渲染出 WebGL 画布（唯一画布，2D 棋盘已移除）
  await expect(page.locator('canvas')).toHaveCount(1)

  // 交互提示文案
  await expect(page.getByText('左键：编辑 / 点选')).toBeVisible()
})
