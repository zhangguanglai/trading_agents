import { useEffect, useRef, useCallback } from 'react'
import { useAnalysisStore } from '@/stores/analysisStore'
import type { AnalysisReport, RiskItem, KeyMetric } from '@/types'
import { getBaseUrl } from '@/services/api'

const MAX_RECONNECT_ATTEMPTS = 12
const BASE_RECONNECT_DELAY_MS = 2000

export function useSSE(jobId: string | null) {
    const eventSourceRef = useRef<EventSource | null>(null)
    const agentMessageMapRef = useRef<Record<string, string>>({})
    const firstTokenMapRef = useRef<Record<string, boolean>>({})
    const reconnectAttemptsRef = useRef(0)
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const isTerminalRef = useRef(false)

    const {
        setIsConnected,
        updateAgentStatus,
        updateAgentSnapshot,
        addAgentMessage,
        addAgentToolCall,
        addAgentReport,
        addAgentToken,
        addLog,
        setReport,
        setStructuredData,
        setIsAnalyzing,
        addChatMessage,
        appendToChatMessage,
        setMessageContent,
    } = useAnalysisStore()

    const connect = useCallback(() => {
        if (!jobId || eventSourceRef.current) return
        if (isTerminalRef.current) return

        const url = `${getBaseUrl()}/v1/jobs/${jobId}/events`
        const eventSource = new EventSource(url)
        eventSourceRef.current = eventSource

        const parseData = (raw: string): Record<string, unknown> | null => {
            if (!raw || raw === '[DONE]') return null
            try {
                return JSON.parse(raw) as Record<string, unknown>
            } catch (error) {
                console.error('Failed to parse SSE payload:', error, raw)
                return null
            }
        }

        const handleEvent = (eventType: string, data: Record<string, unknown>) => {
            switch (eventType) {
                case 'job.created':
                    addLog({
                        id: Date.now().toString(),
                        timestamp: new Date().toISOString(),
                        type: 'system',
                        content: `Job created: ${String(data.job_id || '')}`
                    })
                    break

                case 'job.running':
                    setIsAnalyzing(true)
                    addChatMessage({
                        id: `job-running-${Date.now()}`,
                        role: 'system',
                        content: String(data.msg || `🚀 深度投研分析已启动...`),
                        timestamp: new Date().toISOString()
                    })
                    addLog({
                        id: Date.now().toString(),
                        timestamp: new Date().toISOString(),
                        type: 'system',
                        content: `Analysis started for ${String(data.symbol || '')}`
                    })
                    break

                case 'job.completed':
                    isTerminalRef.current = true
                    setIsAnalyzing(false)
                    setReport((data.result || null) as AnalysisReport | null)
                    setStructuredData({
                        riskItems: (data.risk_items as RiskItem[] | undefined) ?? [],
                        keyMetrics: (data.key_metrics as KeyMetric[] | undefined) ?? [],
                        confidence: data.confidence as number | null | undefined,
                        targetPrice: data.target_price as number | null | undefined,
                        stopLoss: data.stop_loss_price as number | null | undefined,
                    })
                    addChatMessage({
                        id: `job-complete-${Date.now()}`,
                        role: 'system',
                        content: `✅ 分析完成。最终建议：${String(data.decision || '')}`,
                        timestamp: new Date().toISOString()
                    })
                    addLog({
                        id: Date.now().toString(),
                        timestamp: new Date().toISOString(),
                        type: 'system',
                        content: `Analysis completed. Decision: ${String(data.decision || '')}`
                    })
                    break

                case 'job.failed':
                    isTerminalRef.current = true
                    setIsAnalyzing(false)
                    addChatMessage({
                        id: `job-failed-${Date.now()}`,
                        role: 'system',
                        content: `❌ 分析失败: ${String(data.error || '未知错误')}`,
                        timestamp: new Date().toISOString()
                    })
                    addLog({
                        id: Date.now().toString(),
                        timestamp: new Date().toISOString(),
                        type: 'error',
                        content: `Analysis failed: ${String(data.error || 'unknown error')}`
                    })
                    break

                case 'agent.status':
                    const statusData = data as { agent: string; status: string; horizon?: string }
                    if (statusData.status === 'in_progress') {
                        const agentName = statusData.agent
                        const horizon = statusData.horizon ? `(${statusData.horizon === 'short' ? '短线' : '中线'})` : ''
                        const msgId = `agent-msg-${agentName}-${statusData.horizon || 'main'}-${Date.now()}`
                        
                        agentMessageMapRef.current[`${agentName}-${statusData.horizon || 'main'}`] = msgId
                        firstTokenMapRef.current[msgId] = true

                        addChatMessage({
                            id: msgId,
                            role: 'assistant',
                            agent: agentName,
                            content: `**${agentName}** ${horizon} 正在思考并撰写报告中...`,
                            timestamp: new Date().toISOString()
                        })
                    }
                    updateAgentStatus(data as { agent: string; status: 'pending' | 'in_progress' | 'completed' | 'error' | 'skipped'; previous_status?: 'pending' | 'in_progress' | 'completed' | 'error' | 'skipped' })
                    break

                case 'agent.snapshot':
                    updateAgentSnapshot(data as { agents: Array<{ team: string; agent: string; status: 'pending' | 'in_progress' | 'completed' | 'error' | 'skipped' }> })
                    break

                case 'agent.token':
                    const tokenData = data as { agent: string; report: string; token: string; horizon?: string }
                    const agentKey = `${tokenData.agent}-${tokenData.horizon || 'main'}`
                    const targetMsgId = agentMessageMapRef.current[agentKey]
                    
                    if (targetMsgId) {
                        if (firstTokenMapRef.current[targetMsgId]) {
                            // 第一个 token 时，清除占位文字
                            const horizonText = tokenData.horizon ? `(${tokenData.horizon === 'short' ? '短线' : '中线'})` : ''
                            setMessageContent(targetMsgId, `### ${tokenData.agent} ${horizonText}\n\n${tokenData.token}`)
                            firstTokenMapRef.current[targetMsgId] = false
                        } else {
                            appendToChatMessage(targetMsgId, tokenData.token)
                        }
                    }
                    addAgentToken(data as { agent: string; report: string; token: string; horizon?: string })
                    break

                case 'agent.message':
                    addAgentMessage(data as { agent: string | null; message_type: string | null; content: string })
                    break

                case 'agent.tool_call':
                    addAgentToolCall(data as { agent: string | null; tool_call: { name: string; args: Record<string, unknown> } })
                    break

                case 'agent.report':
                    addAgentReport(data as { section: string; content: string })
                    break
            }
        }

        eventSource.onopen = () => {
            reconnectAttemptsRef.current = 0
            setIsConnected(true)
            addLog({
                id: Date.now().toString(),
                timestamp: new Date().toISOString(),
                type: 'system',
                content: 'Connected to analysis stream'
            })
        }

        // backend emits named SSE events, so we must register listeners per event name
        const eventNames = [
            'job.ready',
            'job.created',
            'job.running',
            'job.completed',
            'job.failed',
            'agent.status',
            'agent.snapshot',
            'agent.message',
            'agent.tool_call',
            'agent.report',
            'agent.token',
            'done',
            'ping',
        ]

        eventNames.forEach((name) => {
            eventSource.addEventListener(name, (evt: MessageEvent) => {
                if (name === 'ping') return // Ignore ping events, they just keep connection alive
                if (name === 'done' || evt.data === '[DONE]') {
                    eventSource.close()
                    eventSourceRef.current = null
                    setIsConnected(false)
                    setIsAnalyzing(false)
                    return
                }
                const payload = parseData(evt.data)
                if (!payload) return
                handleEvent(name, payload)
            })
        })

        eventSource.onerror = (error) => {
            console.error('SSE error:', error)
            setIsConnected(false)
            if (isTerminalRef.current) {
                eventSource.close()
                eventSourceRef.current = null
                return
            }
            addLog({
                id: Date.now().toString(),
                timestamp: new Date().toISOString(),
                type: 'error',
                content: 'Connection error, attempting to reconnect...'
            })

            // 指数退避重连，最多重试 MAX_RECONNECT_ATTEMPTS 次
            reconnectAttemptsRef.current += 1
            if (reconnectAttemptsRef.current > MAX_RECONNECT_ATTEMPTS) {
                addLog({
                    id: Date.now().toString(),
                    timestamp: new Date().toISOString(),
                    type: 'error',
                    content: 'Max reconnection attempts reached. Please refresh the page.'
                })
                setIsAnalyzing(false)
                eventSource.close()
                eventSourceRef.current = null
                return
            }

            const delay = Math.min(
                BASE_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttemptsRef.current - 1),
                30000
            )
            if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
            reconnectTimerRef.current = setTimeout(() => {
                if (eventSourceRef.current?.readyState === EventSource.CLOSED && !isTerminalRef.current) {
                    eventSourceRef.current = null
                    connect()
                }
            }, delay)
        }

        return () => {
            if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
            eventSource.close()
            eventSourceRef.current = null
            setIsConnected(false)
        }
    }, [jobId])

    useEffect(() => {
        isTerminalRef.current = false
        reconnectAttemptsRef.current = 0
        const cleanup = connect()
        return () => {
            cleanup?.()
        }
    }, [connect])

    const disconnect = useCallback(() => {
        isTerminalRef.current = true
        if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
        if (eventSourceRef.current) {
            eventSourceRef.current.close()
            eventSourceRef.current = null
            setIsConnected(false)
        }
    }, [setIsConnected])

    return { disconnect }
}