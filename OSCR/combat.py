"""This file implements the Combat class"""

from collections import deque
from datetime import datetime

import numpy

from .datamodels import CritterMeta, DetectionInfo, LogLine, OverviewTableRow, TreeItem, TreeModel
from .detection import Detection
from .utilities import get_entity_name, get_player_handle, datetime_to_display


def check_difficulty_deaths(
            difficulty_data: dict, combat_meta: dict[str, CritterMeta]) -> DetectionInfo:
    """
    Check deaths against combat metadata
    - :param difficulty_data: difficulty-based dictionary from `MAP_DIFFICULTY_ENTITY_DEATH_COUNTS`\
    containing required death counts of specific entities
    - :param combat_meta: Combat metadata generated by `Combat.detect_map`
    """
    for entity_name, required_death_count in difficulty_data.items():
        entity_metadata = combat_meta.get(entity_name)
        if entity_metadata is None:
            # Map is missing some NPC data - it's invalid.
            return DetectionInfo(False, 'difficulty', (entity_name,), step='existence')
        if required_death_count > 0:
            valid = required_death_count == entity_metadata.count
        else:
            valid = entity_metadata.count != 0
        if not valid:
            return DetectionInfo(
                    False, 'difficulty', (entity_name,), required_death_count,
                    entity_metadata.count, 'deaths')
    return DetectionInfo(True, 'difficulty', tuple(difficulty_data.keys()), step='deaths')


def check_difficulty_damage(
            difficulty_data: dict, combat_meta: dict[str, CritterMeta]) -> DetectionInfo:
    """
    Check hull damage taken against combat metadata
    - :param difficulty_data: difficulty-based dicitionary from `MAP_DIFFICULTY_ENTITY_HULL_COUNTS`\
    containing required hull capacity of specific entities
    - :param combat_meta: Combat metadata generated by `Combat.detect_map`
    """
    # only look at the lower variance.
    var = 0.20

    for entity_name, hull_value in difficulty_data.items():
        entity_metadata = combat_meta.get(entity_name)
        if entity_metadata is None:
            # Map is missing some NPC data - it's invalid.
            return DetectionInfo(False, 'difficulty', step='damage')
        med = numpy.percentile(entity_metadata.hull_values, 50)
        low = hull_value * (1 - var)
        valid = low < med
        if not valid:
            return DetectionInfo(False, 'difficulty', (entity_name,), low, med, 'damage')
    return DetectionInfo(True, 'difficulty', tuple(difficulty_data.keys()), step='damage')


class Combat:
    """
    Contains a single combat including raw log lines, map and combat information and shallow parse
    results.
    """

    def __init__(self, graph_resolution: float = 0.2, id: int = -1, log_file: str = ''):
        self.log_data: deque[LogLine] = deque()
        self.id: int = id
        self.map = None
        self.difficulty = None
        self.start_time: datetime = None
        self.end_time: datetime = None
        self.file_pos: list[int, int] = [None, None]
        self.log_file: str = log_file
        self.meta: dict = {
            'log_duration': None,
            'player_duration': None,
            'detection_info': None  # iterable of DetectionInfo
        }
        self.players: dict[str, OverviewTableRow] = dict()
        self.critters: dict = dict()
        self.critter_meta: dict = dict()
        self.graph_resolution = graph_resolution
        self.overview_graphs: dict = dict()
        self.damage_out: TreeModel = None
        self.damage_in: TreeModel = None
        self.heals_out: TreeModel = None
        self.heals_in: TreeModel = None

    @property
    def root_items(self):
        """Root items of analysis tree data: Damage Out, Damage In, Heals Out, Heals In"""
        return (
            self.damage_out._root,
            self.damage_in._root,
            self.heals_out._root,
            self.heals_in._root
        )

    @property
    def description(self):
        if self.difficulty is None:
            return f'{self.map} {datetime_to_display(self.start_time)}'
        return (
            f'{self.map} ({self.difficulty} Difficulty) at '
            + datetime_to_display(self.start_time)
        )

    def create_overview_graphs(self, player: OverviewTableRow, combat_interval: tuple[int, int]):
        """
        creates overview graphs from damage graph

        Parameters:
        - :param player: player to create graphs for
        - :param combat_interval: first and last graph point of the respective player (a graph
        point is an interval of length `graph_resolution` counted from the beginning of the log)
        """
        graph = self.overview_graphs[player.handle]
        player.DMG_graph_data = dmg_graph = graph[combat_interval[0]:combat_interval[1] + 1]
        first_graph_time = self.graph_resolution * (combat_interval[0] + 1)
        last_graph_time = self.graph_resolution * (combat_interval[1] + 1)
        player.graph_time = numpy.linspace(first_graph_time, last_graph_time, len(dmg_graph))
        combat_time_array = player.graph_time - self.graph_resolution * combat_interval[0]
        player.DPS_graph_data = dmg_graph.cumsum() / combat_time_array

    def create_overview(self, overview_graph_intervals: dict[str, tuple[int, int]]):
        """
        Creates overview table from analysis data and overview graphs from self.overiew_graphs and
        creates players with that data in self.players

        Parameters:
        - :overview_graph_intervals: maps player handles to their active combat start and end times
        measured in graph points (1 / graph_resolution points per second of the log)
        """
        combat_duration = (self.end_time - self.start_time).total_seconds()
        total_damage_out = 0
        total_attacks_in = 0
        total_damage_in = 0
        total_heals = 0
        # build_detection_abilities = tuple(Detection.BUILD_DETECTION_ABILITIES.keys())

        for player_item in self.damage_out._player._children:
            dmg_out_data = player_item.data
            player_combat_duration = dmg_out_data[19]
            if player_combat_duration <= 0:
                continue
            player_name_handle_id = dmg_out_data[0]
            player = OverviewTableRow(player_name_handle_id[0], player_name_handle_id[1])
            player.DPS = dmg_out_data[1]
            player.combat_time = player_combat_duration
            player.combat_time_share = player_combat_duration / combat_duration
            player.total_damage = dmg_out_data[2]
            total_damage_out += dmg_out_data[2]
            player.debuff = dmg_out_data[3]
            player.max_one_hit = dmg_out_data[4]
            player.crit_chance = dmg_out_data[5]
            player.total_attacks = dmg_out_data[9]
            player.hull_attacks = dmg_out_data[20]
            player.crit_num = dmg_out_data[11]
            player.misses = dmg_out_data[10]
            dmg_in_data = self.get_player_item(self.damage_in, player_name_handle_id)
            if dmg_in_data is not None:
                dmg_in_data = dmg_in_data.data
                player.deaths = dmg_in_data[8]
                player.total_damage_taken = dmg_in_data[2]
                total_damage_in += dmg_in_data[2]
                player.total_hull_damage_taken = dmg_in_data[15]
                player.total_shield_damage_taken = dmg_in_data[13]
                player.attacks_in_num = dmg_in_data[9]
                total_attacks_in += dmg_in_data[9]
            heal_out_data = self.get_player_item(self.heals_out, player_name_handle_id)
            if heal_out_data is not None:
                heal_out_data = heal_out_data.data
                player.total_heals = heal_out_data[2]
                total_heals += heal_out_data[2]
                player.heal_crit_chance = heal_out_data[8]
                player.heal_crit_num = heal_out_data[10]
                player.heal_num = heal_out_data[9]
            if player.handle in overview_graph_intervals:
                self.create_overview_graphs(player, overview_graph_intervals[player.handle])
            # +++ Currently not used +++
            # build = 'Unknown'
            # for ability_item in player_item._children:
            #     ability_name = ability_item.data[0]
            #     for detection_ability in build_detection_abilities:
            #         if detection_ability in ability_name:
            #             build = Detection.BUILD_DETECTION_ABILITIES[detection_ability]
            #             break
            #     if build != 'Unknown':
            #         break
            # player.build = build
            self.players[player_name_handle_id[0] + player_name_handle_id[1]] = player

        for player in self.players.values():
            try:
                player.heal_share = player.total_heals / total_heals
            except ZeroDivisionError:
                player.heal_share = 0.0
            try:
                player.attacks_in_share = player.attacks_in_num / total_attacks_in
            except ZeroDivisionError:
                player.attacks_in_share = 0.0
            try:
                player.taken_damage_share = player.total_damage_taken / total_damage_in
            except ZeroDivisionError:
                player.taken_damage_share = 0.0
            try:
                player.damage_share = player.total_damage / total_damage_out
            except ZeroDivisionError:
                player.damage_share = 0.0

    def map_is_hive_space(self):
        """
        Checks whether the map is Hive Space by checking for existence of a certain entity.
        """
        for critter in self.damage_in._npc._children:
            if 'Space_Borg_Dreadnought_Hive_Intro' in critter.data.id[0]:
                return True
        return False

    def get_player_item(self, model: TreeModel, name_and_handle: tuple) -> TreeItem | None:
        """
        returns the player item identified by `name_and_handle` from `model`
        """
        for player in model._player._children:
            if player.data[0] == name_and_handle:
                return player

    def analyze_last_line(self):
        """Analyze the last line and try and detect the map and difficulty"""

        if (
            self.map is not None
            and self.map != "Combat"
            and self.difficulty is not None
        ):
            return

        _map, _difficulty = Detection.detect_line(self.log_data[0])

        if self.map is None or self.map == "Combat":
            self.map = _map

        if self.difficulty is None:
            self.difficulty = _difficulty

    def analyze_shallow(self, graph_resolution=0.2):
        """
        Do a shallow combat analysis
        The goal of the shallow combat analysis is to get as much data
        as we can in a single iteration of the log data. This includes building
        damage over time graphs so that the log does not need to be iterated
        over again.
        """

        self.graph_resolution = graph_resolution
        self.analyze_players()
        self.analyze_critters()

    def analyze_players(self):
        """
        Analyze players to determine time-based metrics such as DPS.
        """

        total_damage = 0
        total_damage_taken = 0
        total_attacks = 0
        total_heals = 0

        # Filter out players with no combat time.
        players = {}
        for key, player in self.players.items():
            if player.combat_interval is not None and player.events is not None:
                players[key] = player
        self.players = players

        for player in self.players.values():
            total_damage += player.total_damage
            total_damage_taken += player.total_damage_taken
            total_attacks += player.attacks_in_num
            total_heals += player.total_heals

        for player in self.players.values():
            player.combat_time = player.combat_interval[1] - player.combat_interval[0]
            successful_attacks = player.hull_attacks - player.misses

            try:
                player.debuff = (player.total_damage / player.base_damage - 1) * 100
            except ZeroDivisionError:
                player.debuff = 0.0
            try:
                player.DPS = player.total_damage / player.combat_time
            except ZeroDivisionError:
                player.DPS = 0.0
            if successful_attacks > 0:
                player.crit_chance = player.crit_num / successful_attacks * 100
            else:
                player.crit_chance = 0
            try:
                player.heal_crit_chance = player.heal_crit_num / player.heal_num * 100
            except ZeroDivisionError:
                player.heal_crit_chance = 0.0

            try:
                player.damage_share = player.total_damage / total_damage * 100
            except ZeroDivisionError:
                player.damage_share = 0.0
            try:
                player.taken_damage_share = (
                    player.total_damage_taken / total_damage_taken * 100
                )
            except ZeroDivisionError:
                player.taken_damage_share = 0.0
            try:
                player.attacks_in_share = player.attacks_in_num / total_attacks * 100
            except ZeroDivisionError:
                player.attacks_in_share = 0.0
            try:
                player.heal_share = player.total_heals / total_heals * 100
            except ZeroDivisionError:
                player.heal_share = 0.0

            for k, v in Detection.BUILD_DETECTION_ABILITIES.items():
                for event in player.events:
                    if k in event:
                        player.build = v
                        break

            player.graph_time = tuple(
                map(lambda x: round(x, 1), player.graph_time))
            DPS_data = numpy.array(player.DMG_graph_data,
                                   dtype=numpy.float64).cumsum()
            player.DPS_graph_data = tuple(DPS_data / player.graph_time)

    def detect_map(self):
        """
        Analyzes the entities on the parser to determine:
            - The map type
            - The difficulty of the map
        Requires combat to be fully analyzed
        Fills `self.meta['detection_info']` with information about the detection
        """
        self.map = ''
        critters: dict[str, CritterMeta] = dict()
        for entity in self.damage_in._npc._children:
            entity_name = get_entity_name(entity.data[0][2])
            if entity_name in critters:
                critters[entity_name].add_critter(entity.data[2])
            else:
                critters[entity_name] = CritterMeta(entity_name, 1, [entity.data[2]])

        # contains multiple values when multiple entities identify the same map
        map_identificators = critters.keys() & Detection.MAP_IDENTIFIERS_EXISTENCE.keys()
        if len(map_identificators) < 1:
            self.map = 'Combat'
            self.difficulty = None
            self.meta['detection_info'] = [DetectionInfo(False, step='existence')]
            return
        detection_info = list()
        for identificator in map_identificators:
            map_data = Detection.MAP_IDENTIFIERS_EXISTENCE[identificator]
            if map_data['difficulty'] != 'Any':
                self.map = map_data['map']
                self.difficulty = map_data['difficulty']
                self.meta['detection_info'] = [DetectionInfo(
                        True, 'both', (identificator,), step='existence', map=self.map,
                        difficulty=self.difficulty)]
                return
            self.map = map_data['map']
        detection_info.append(
                DetectionInfo(True, 'map', (identificator,), step='existence', map=self.map))

        for diffic, meta in Detection.MAP_DIFFICULTY_ENTITY_DEATH_COUNTS.get(self.map, {}).items():
            if detection_meta := check_difficulty_deaths(meta, critters):
                self.difficulty = diffic
            detection_meta.difficulty = diffic
            detection_info.append(detection_meta)

        for diffic, meta in Detection.MAP_DIFFICULTY_ENTITY_HULL_COUNTS.get(self.map, {}).items():
            if detection_meta := check_difficulty_damage(meta, critters):
                self.difficulty = diffic
            detection_meta.difficulty = diffic
            detection_info.append(detection_meta)
        self.meta['detection_info'] = detection_info

    def analyze_critters(self):
        """
        Analyze map entities Computers to determine:
            - The map type
            - The difficulty of the map

        If new results are obtained, this overrides the values previously set
        if detect_line() was called during the creation of the Combat object.

        The algorithm starts broad and then narrows in if additional detections
        are necessary. The order in which they are processed:
            - Entity Counts
            - Entity Hull Damage Taken

        On maps such as Infected Space Entity Hull Damage Taken
        (and later steps) do not need to be provided if Entity Counts is
        sufficient in determinning map valididty.

        Assumes that map and difficulty have already been set with detect_line.
        """

        _difficulty = self.difficulty

        if self.map and self.difficulty != "Any":
            return

        if self.map == "Combat":
            return

        for entity_id, entity in self.critters.items():
            entity_name = get_entity_name(entity_id)
            self.add_entity_to_critter_meta(entity_name)
            self.critter_meta[entity_name]["count"] += 1
            self.critter_meta[entity_name]["deaths"] += entity.deaths
            total_hull_damage_taken = self.critter_meta[entity_name][
                "total_hull_damage_taken"
            ]
            total_hull_damage_taken.append(entity.total_hull_damage_taken)

        # Death Detection
        data = Detection.MAP_DIFFICULTY_ENTITY_DEATH_COUNTS.get(self.map)
        if data is None:
            self.difficulty = _difficulty
            return

        matched = False
        for difficulty, entry in data.items():
            if check_difficulty_deaths(difficulty, entry, self.critter_meta):
                matched = True
                _difficulty = difficulty
            else:
                continue

        if not matched:
            return

        # Hull Detection
        data = Detection.MAP_DIFFICULTY_ENTITY_HULL_COUNTS.get(self.map)
        if data is None:
            self.difficulty = _difficulty
            return

        matched = False
        for difficulty, entry in data.items():
            if check_difficulty_damage(difficulty, entry, self.critter_meta):
                matched = True
                _difficulty = difficulty
            else:
                continue

        if not matched:
            return

        self.difficulty = _difficulty

    def add_entity_to_critter_meta(self, entity_name):
        """Adds a new entry to the critter metadata"""
        if self.critter_meta.get(entity_name) is None:
            self.critter_meta[entity_name] = {
                "count": 0,
                "deaths": 0,
                "total_hull_damage_taken": [],
            }

    @property
    def duration(self):
        return self.end_time - self.start_time

    @property
    def date_time(self):
        """Returns the end time - for compatibility with previous versions"""
        return self.end_time

    @property
    def player_dict(self):
        """Returns the list of players - for compatibility with previous versions"""
        return self.players

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} - Map: {self.map} - Difficulty: {self.difficulty} - "
            f"Datetime: {self.start_time}>"
        )

    def __gt__(self, other):
        if not isinstance(other, Combat):
            raise TypeError(
                f"Cannot compare {self.__class__.__name__} to {
                    other.__class__.__name__}"
            )
        if isinstance(self.date_time, datetime) and isinstance(
            self.date_time, datetime
        ):
            return self.date_time > other.date_time
        if not isinstance(self.date_time, datetime) and isinstance(
            self.date_time, datetime
        ):
            return False
        if isinstance(self.date_time, datetime) and not isinstance(
            other.date_time, datetime
        ):
            return True
