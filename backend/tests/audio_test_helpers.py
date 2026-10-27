class FakeReadingAudioRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def get(self, target_key: str):
        return self.strings.get(target_key)

    def set(self, target_key: str, target_val: str, ex=None):
        self.strings[target_key] = str(target_val)

    def delete(self, *target_keys):
        for key_item in target_keys:
            self.strings.pop(key_item, None)
            self.sets.pop(key_item, None)
            self.hashes.pop(key_item, None)

    def incr(self, target_key: str):
        current_val = int(self.strings.get(target_key, 0)) + 1
        self.strings[target_key] = str(current_val)
        return current_val

    def decr(self, target_key: str):
        current_val = int(self.strings.get(target_key, 0)) - 1
        self.strings[target_key] = str(current_val)
        return current_val

    def expire(self, target_key: str, ttl_seconds: int):
        pass

    def scard(self, target_key: str):
        return len(self.sets.get(target_key, set()))

    def smembers(self, target_key: str):
        return set(self.sets.get(target_key, set()))

    def sadd(self, target_key: str, *members):
        target_set = self.sets.setdefault(target_key, set())
        for member_item in members:
            target_set.add(str(member_item))

    def srem(self, target_key: str, *members):
        target_set = self.sets.get(target_key, set())
        for member_item in members:
            target_set.discard(str(member_item))

    def sismember(self, target_key: str, member_item: str):
        return 1 if str(member_item) in self.sets.get(target_key, set()) else 0

    def hget(self, hash_key: str, field_name: str):
        return self.hashes.get(hash_key, {}).get(str(field_name))

    def hset(self, hash_key: str, field_name: str, field_value: str):
        self.hashes.setdefault(hash_key, {})[str(field_name)] = str(field_value)

    def hincrby(self, hash_key: str, field_name: str, increment_amount: int):
        existing_val = int(self.hashes.setdefault(hash_key, {}).get(str(field_name), 0))
        new_val = existing_val + increment_amount
        self.hashes[hash_key][str(field_name)] = str(new_val)
        return new_val

    def hdel(self, hash_key: str, *fields):
        removed_count = 0
        hash_dict = self.hashes.get(hash_key, {})
        for field_item in fields:
            if str(field_item) in hash_dict:
                del hash_dict[str(field_item)]
                removed_count += 1
        return removed_count

    def hlen(self, hash_key: str):
        return len(self.hashes.get(hash_key, {}))

    def hexists(self, hash_key: str, field_name: str):
        return 1 if str(field_name) in self.hashes.get(hash_key, {}) else 0

    def hkeys(self, hash_key: str):
        return list(self.hashes.get(hash_key, {}).keys())

    def eval(self, lua_script: str, numkeys: int, *eval_arguments):
        keys_slice = eval_arguments[:numkeys]
        args_slice = eval_arguments[numkeys:]

        if "all_session_flag" in lua_script and "safe_to_delete" in lua_script:
            user_key = keys_slice[0]
            session_chunks_key = keys_slice[1]
            target_user_id = str(args_slice[0])
            active_session_id = str(args_slice[1])
            all_session_flag = str(args_slice[2])
            cache_id_count = int(args_slice[3])

            existing_user = self.get(user_key)
            if existing_user and existing_user != target_user_id:
                return [-1]

            target_ids = []
            if all_session_flag == "1":
                target_ids = list(self.hkeys(session_chunks_key))
                for target_index in range(cache_id_count):
                    target_ids.append(str(args_slice[5 + target_index]))
                self.delete(session_chunks_key, user_key)
                self.set(f"tts:closed:{active_session_id}", "1")
            else:
                for target_index in range(cache_id_count):
                    cid_item = str(args_slice[5 + target_index])
                    references = int(self.hget(session_chunks_key, cid_item) or 0)
                    if references > 1:
                        self.hincrby(session_chunks_key, cid_item, -1)
                    elif references == 1:
                        self.hdel(session_chunks_key, cid_item)
                        target_ids.append(cid_item)
                    else:
                        target_ids.append(cid_item)
                if self.hlen(session_chunks_key) == 0:
                    self.delete(session_chunks_key, user_key)

            safe_ids = []
            for cid_str in target_ids:
                chunk_key = f"tts:chunk_sessions:{cid_str}"
                self.srem(chunk_key, active_session_id)
                for owner in list(self.smembers(chunk_key)):
                    if not self.hexists(f"tts:session_chunks:{owner}", cid_str):
                        self.srem(chunk_key, owner)
                remaining_count = self.scard(chunk_key)
                reader_val = int(self.get(f"tts:reader:{cid_str}") or 0)
                if remaining_count == 0 and reader_val <= 0:
                    safe_ids.append(int(cid_str))
            return safe_ids

        if "target_cache_id" in lua_script and "chunk_sessions_key" in lua_script:
            user_key = keys_slice[0]
            session_chunks_key = keys_slice[1]
            chunk_sessions_key = keys_slice[2]
            target_user_id = str(args_slice[0])
            active_session_id = str(args_slice[1])
            target_cache_id = str(args_slice[2])

            if self.get(f"tts:closed:{active_session_id}") is not None:
                return -2

            existing_user = self.get(user_key)
            if existing_user and existing_user != target_user_id:
                return -1

            self.set(user_key, target_user_id)
            self.hincrby(session_chunks_key, target_cache_id, 1)
            self.sadd(chunk_sessions_key, active_session_id)
            return 1

        if "held_chunks" in lua_script:
            user_key = keys_slice[0]
            target_user_id = str(args_slice[0])
            existing_user = self.get(user_key)
            if not existing_user or existing_user != target_user_id:
                return 0
            return 1

        if "redis.call('INCR'" in lua_script or "'INCR'" in lua_script:
            key_name = keys_slice[0]
            return self.incr(key_name)

        if "redis.call('DECR'" in lua_script or "'DECR'" in lua_script:
            key_name = keys_slice[0]
            current_value = self.decr(key_name)
            if current_value <= 0:
                self.delete(key_name)
                return 0
            return current_value

        return 0
