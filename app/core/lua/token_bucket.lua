local rpm_key = KEYS[1]
local tpm_key = KEYS[2]

local now = tonumber(ARGV[1])
local rpm_limit = tonumber(ARGV[2])
local tpm_limit = tonumber(ARGV[3])
local tpm_estimate = tonumber(ARGV[4])

-- Get RPM bucket state
local rpm_data = redis.call('HMGET', rpm_key, 'tokens', 'last_updated')
local rpm_tokens = tonumber(rpm_data[1])
local rpm_last = tonumber(rpm_data[2])

if not rpm_tokens then
    rpm_tokens = rpm_limit
    rpm_last = now
else
    local elapsed = now - rpm_last
    if elapsed > 0 then
        local replenish = elapsed * (rpm_limit / 60.0)
        rpm_tokens = math.min(rpm_limit, rpm_tokens + replenish)
        rpm_last = now
    end
end

-- Get TPM bucket state
local tpm_data = redis.call('HMGET', tpm_key, 'tokens', 'last_updated')
local tpm_tokens = tonumber(tpm_data[1])
local tpm_last = tonumber(tpm_data[2])

if not tpm_tokens then
    tpm_tokens = tpm_limit
    tpm_last = now
else
    local elapsed = now - tpm_last
    if elapsed > 0 then
        local replenish = elapsed * (tpm_limit / 60.0)
        tpm_tokens = math.min(tpm_limit, tpm_tokens + replenish)
        tpm_last = now
    end
end

-- Check if we have enough tokens
if rpm_tokens >= 1 and tpm_tokens >= tpm_estimate then
    -- Deduct tokens
    rpm_tokens = rpm_tokens - 1
    tpm_tokens = tpm_tokens - tpm_estimate
    
    redis.call('HMSET', rpm_key, 'tokens', rpm_tokens, 'last_updated', rpm_last)
    redis.call('EXPIRE', rpm_key, 60)
    
    redis.call('HMSET', tpm_key, 'tokens', tpm_tokens, 'last_updated', tpm_last)
    redis.call('EXPIRE', tpm_key, 60)
    
    return {1, 0} -- Allowed, retry_after = 0
else
    -- Calculate retry after in seconds
    local rpm_shortage = 1 - rpm_tokens
    local tpm_shortage = tpm_estimate - tpm_tokens
    
    local rpm_wait = 0
    if rpm_shortage > 0 then
        rpm_wait = rpm_shortage / (rpm_limit / 60.0)
    end
    
    local tpm_wait = 0
    if tpm_shortage > 0 then
        tpm_wait = tpm_shortage / (tpm_limit / 60.0)
    end
    
    local rejection_type = "rpm"
    if tpm_wait > rpm_wait then
        rejection_type = "tpm"
    end
    
    local retry_after = math.max(rpm_wait, tpm_wait)
    return {0, math.ceil(retry_after), rejection_type} -- Blocked, retry_after, rejection_type
end
