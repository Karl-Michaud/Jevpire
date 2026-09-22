"""Trajectory maths. The anchoring convention here is the project's most expensive detail.

Savant's own published plate_x/plate_z are the reference: reconstructing them from the
trajectory must land within rounding error. Getting the anchor wrong costs 0.45 ft --
wider than the ball.
"""

from __future__ import annotations

import math

import pytest

from jevpire.schema import (
    ABS_ZONE_BOT_FRAC,
    ABS_ZONE_TOP_FRAC,
    PLATE_FRONT_Y,
    PLATE_MIDDLE_Y,
    TRAJECTORY_ANCHOR_Y,
    PitchTrajectory,
    StrikeZone,
)

# A real pitch: Zack Wheeler four-seamer, game 823452, at-bat 1 pitch 1.
# StatsAPI reports pX=-0.983, pZ=2.032 at the FRONT of the plate.
WHEELER = dict(
    x0=-1.7190325186283286, y0=50.00031955404647, z0=4.946303003830063,
    vx0=5.540452839958165, vy0=-140.85007645851383, vz0=-5.016942844475454,
    ax=-19.443308929248094, ay=30.46528095658877, az=-17.299160026643303,
)
WHEELER_FRONT = (-0.983, 2.032)


@pytest.fixture
def traj() -> PitchTrajectory:
    return PitchTrajectory(**WHEELER)


def test_reproduces_statsapi_front_of_plate_location(traj):
    """StatsAPI's pX/pZ are front-of-plate. Ours must match to 3 decimals."""
    x, z = traj.location_at_plane(PLATE_FRONT_Y)
    assert x == pytest.approx(WHEELER_FRONT[0], abs=1e-3)
    assert z == pytest.approx(WHEELER_FRONT[1], abs=1e-3)


def test_middle_of_plate_differs_from_front_by_a_meaningful_amount(traj):
    """The 2026 redefinition. A falling ball is lower at the middle than at the front."""
    _, z_front = traj.location_at_plane(PLATE_FRONT_Y)
    _, z_mid = traj.location_at_plane(PLATE_MIDDLE_Y)
    drop_in = (z_front - z_mid) * 12
    assert z_mid < z_front
    assert 0.3 < drop_in < 2.0, f"drop of {drop_in:.2f} in is outside the plausible range"


def test_time_to_plate_is_physically_plausible(traj):
    t_release = traj.time_to(54.0)
    t_plate = traj.time_to(PLATE_FRONT_Y)
    flight = t_plate - t_release
    assert t_release < 0, "release happens before the y=50 anchor, i.e. at negative t"
    assert 0.35 < flight < 0.55, f"flight time {flight:.3f}s is implausible"


def test_ball_decelerates(traj):
    """Drag. The ball is always slower at the plate than out of the hand."""
    assert traj.speed_at_time(traj.time_to(PLATE_FRONT_Y)) < traj.speed_at_time(traj.time_to(54.0))


def test_release_speed_is_in_the_right_ballpark(traj):
    mph = traj.speed_at_time(traj.time_to(54.0))
    assert 60 < mph < 106, f"{mph:.1f} mph"


def test_vy0_is_negative(traj):
    assert traj.vy0 < 0


def test_ay_is_positive(traj):
    """Drag opposes motion in -y, so the y-acceleration term is positive."""
    assert traj.ay > 0


def test_location_at_time_is_consistent_with_location_at_plane(traj):
    t = traj.time_to(PLATE_MIDDLE_Y)
    x, y, z = traj.location_at_time(t)
    xp, zp = traj.location_at_plane(PLATE_MIDDLE_Y)
    assert y == pytest.approx(PLATE_MIDDLE_Y, abs=1e-9)
    assert (x, z) == pytest.approx((xp, zp), abs=1e-12)


# --- the Savant parameterisation ------------------------------------------------------


def test_from_release_anchored_round_trips(traj):
    """Savant gives the release point, not the y=50 anchor. Back-solving must recover it."""
    t_rel = traj.time_to(53.22)
    rx, ry, rz = traj.location_at_time(t_rel)

    rebuilt = PitchTrajectory.from_release_anchored(
        release_x=rx, release_y=ry, release_z=rz,
        vx0=traj.vx0, vy0=traj.vy0, vz0=traj.vz0,
        ax=traj.ax, ay=traj.ay, az=traj.az,
    )
    # The rebuilt fit is anchored at exactly y=50, while MLB's is at y=50.00032. Both
    # describe the same parabola, sampled at slightly different reference planes, so the
    # anchor coordinates differ by a hair. What must agree is the PHYSICS.
    assert rebuilt.y0 == pytest.approx(TRAJECTORY_ANCHOR_Y, abs=1e-9)
    assert rebuilt.x0 == pytest.approx(traj.x0, abs=1e-3)
    assert rebuilt.z0 == pytest.approx(traj.z0, abs=1e-3)

    ax_, az_ = rebuilt.location_at_plane(PLATE_MIDDLE_Y)
    bx, bz = traj.location_at_plane(PLATE_MIDDLE_Y)
    err_in = math.hypot(ax_ - bx, az_ - bz) * 12
    assert err_in < 0.01, f"plate location differs by {err_in:.4f} in"


def test_naive_anchoring_is_badly_wrong(traj):
    """Guard the mistake this API exists to prevent.

    Treating release_pos_* as the y=50 anchor (rather than back-solving) puts the plate
    location out by an amount larger than the ball itself.
    """
    t_rel = traj.time_to(53.22)
    rx, ry, rz = traj.location_at_time(t_rel)

    naive = PitchTrajectory(
        x0=rx, y0=ry, z0=rz,
        vx0=traj.vx0, vy0=traj.vy0, vz0=traj.vz0,
        ax=traj.ax, ay=traj.ay, az=traj.az,
    )
    nx, nz = naive.location_at_plane(PLATE_MIDDLE_Y)
    cx, cz = traj.location_at_plane(PLATE_MIDDLE_Y)
    err_ft = math.hypot(nx - cx, nz - cz)
    assert err_ft > 0.12, f"expected a large error from naive anchoring, got {err_ft:.4f} ft"


def test_plane_outside_the_flight_raises(traj):
    """The parabola "reaches" y=500 at an absurd t. Silently returning it would be a footgun."""
    with pytest.raises(ValueError, match="outside the modelled flight"):
        traj.time_to(500.0)


def test_every_plane_along_the_real_flight_is_accepted(traj):
    for y in (54.0, 50.0, 40.0, 20.0, 5.0, PLATE_FRONT_Y, PLATE_MIDDLE_Y, 0.0):
        t = traj.time_to(y)
        assert abs(t) < 1.0


# --- zone -----------------------------------------------------------------------------


def test_zone_implies_a_sane_batter_height():
    zone = StrikeZone(top=3.369, bottom=1.700)
    h_in = zone.implied_height_ft * 12
    assert 60 < h_in < 84, f"implied height {h_in:.1f} in"


def test_zone_top_and_bottom_come_from_one_height():
    """Both bounds are fractions of the same measured height."""
    height = 6.2972
    zone = StrikeZone(top=ABS_ZONE_TOP_FRAC * height, bottom=ABS_ZONE_BOT_FRAC * height)
    assert zone.implied_height_ft == pytest.approx(height, abs=1e-9)
    assert zone.bottom / ABS_ZONE_BOT_FRAC == pytest.approx(height, abs=1e-9)


def test_zone_constants_match_the_published_rule():
    assert StrikeZone.HALF_WIDTH * 2 * 12 == pytest.approx(17.0)
    assert StrikeZone.BALL_RADIUS * 12 == pytest.approx(1.45)
    assert StrikeZone.PLANE_Y * 12 == pytest.approx(8.5)
